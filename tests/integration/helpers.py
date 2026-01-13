#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
import contextlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict

# TODO jubilant: remove juju, pytest-operator, and pytest-asyncio when all tests migrated
import yaml
from jubilant import Juju
from tenacity import retry, stop_after_attempt, wait_fixed

from literals import (
    CLIENT_PORT,
    INTERNAL_USER,
    INTERNAL_USER_PASSWORD_CONFIG,
    TLS_ROOT_DIR,
    TLSType,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME: str = METADATA["name"]
TLS_NAME = "self-signed-certificates"
GRAFANA_AGENT_APP_NAME = "grafana-agent"
COS_CHANNEL = "1/stable"
LOKI_APP_NAME = "loki"
PROMETHEUS_APP_NAME = "prometheus"
GRAFANA_APP_NAME = "grafana"
COS_RELATION_NAME = "cos-agent"


class SecretNotFoundError(Exception):
    """Raised when a secret is not found."""


def put_key(
    endpoints: str,
    key: str,
    value: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> str:
    """Write data to etcd using `etcdctl`."""
    etcd_command = f"etcdctl put {key} {value} --endpoints={endpoints}"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"

    return subprocess.getoutput(etcd_command).split("\n")[0]


def get_key(
    endpoints: str,
    key: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> str:
    """Read data from etcd using `etcdctl` via `juju ssh`."""
    etcd_command = f"etcdctl get {key} --endpoints={endpoints}"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"

    return subprocess.getoutput(etcd_command).split("\n")[1]


@retry(stop=stop_after_attempt(10), wait=wait_fixed(3), reraise=True)
def get_cluster_members(
    endpoints: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> list[dict]:
    """Query all cluster members from etcd using `etcdctl`."""
    etcd_command = f"etcdctl member list --endpoints={endpoints} -w=json"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"

    try:
        result = subprocess.getoutput(etcd_command).split("\n")[0]
        return json.loads(result)["members"]
    except KeyError:
        raise


@retry(stop=stop_after_attempt(10), wait=wait_fixed(3), reraise=True)
def get_cluster_id(
    endpoints: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> str:
    """Query the cluster id from etcd using `etcdctl`."""
    etcd_command = f"etcdctl endpoint status --endpoints={endpoints} -w=json"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"

    result = subprocess.getoutput(etcd_command).split("\n")
    for r in result:
        member = json.loads(r)
        try:
            return member[0]["Status"]["header"]["cluster_id"]
        except (TypeError, KeyError) as e:
            logger.warning(e)

    raise KeyError("cluster_id not found")


def get_cluster_endpoints(juju: Juju, app_name: str = APP_NAME, tls_enabled: bool = False) -> str:
    """Resolve the etcd endpoints for a given juju application."""
    return ",".join(
        [
            f"{'https' if tls_enabled else 'http'}://{unit.public_address}:{CLIENT_PORT}"
            for unit in juju.status().get_units(app_name).values()
        ]
    )


def get_unit_endpoint(
    juju: Juju,
    unit_name: str,
    app_name: str = APP_NAME,
    tls_enabled: bool = False,
) -> str | None:
    """Resolve the etcd endpoint for a given unit name."""
    for name, details in juju.status().get_units(app_name).items():
        if unit_name == name:
            return f"{'https' if tls_enabled else 'http'}://{details.public_address}:{CLIENT_PORT}"
    return None


def get_remaining_endpoints(all_endpoints: str, endpoints_to_subtract: str) -> str:
    """Subtract one comma-delimited list of endpoints from another."""
    remaining_endpoints = all_endpoints.split(",")
    remaining_endpoints.remove(endpoints_to_subtract)
    return ",".join(remaining_endpoints)


def is_endpoint_up(
    endpoint: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> bool:
    """Check health of an etcd endpoint."""
    etcd_command = f"etcdctl endpoint health --endpoints={endpoint} -w=json"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"

    try:
        result = subprocess.getoutput(etcd_command).split("\n")[0]
        status = json.loads(result)[0]
        return status["health"]
    except Exception:
        return False


@retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True)
def get_raft_leader(
    endpoints: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> str:
    """Query the Raft leader via the `endpoint status` and `member list` commands.

    Returns:
        str: the member-name of the Raft leader, e.g. `etcd42`
    """
    etcd_command = f"etcdctl endpoint status --endpoints={endpoints} -w=json"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
                --cacert client_ca.pem \
                --cert client.pem \
                --key client.key"

    # query leader id
    try:
        result = subprocess.getoutput(etcd_command).split("\n")[0]
        members = json.loads(result)
        leader_id = members[0]["Status"]["leader"]
    except KeyError:
        raise

    # query member name for leader id
    etcd_command = f"etcdctl member list --endpoints={endpoints} -w=json"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
                --cacert client_ca.pem \
                --cert client.pem \
                --key client.key"

    result = subprocess.getoutput(etcd_command).split("\n")[0]
    members = json.loads(result)
    for member in members["members"]:
        if member["ID"] == leader_id:
            return member["name"]


def get_unit_relation_data(
    juju: Juju,
    unit_name: str,
    target_unit_name: str,
    relation_name: str,
    key: str,
) -> str | None:
    """Get relation data for a unit.

    Args:
        juju: An instance of Jubilant's Juju class on which to run Juju commands
        unit_name: The name of provider's unit
        target_unit_name: The name of requirer's unit
        relation_name: name of the relation to get connection data from
        key: key of data to be retrieved

    Returns:
        the data that was requested or None
            if no data in the relation

    Raises:
        ValueError if it's not possible to get application unit data
            or if there is no data for the particular relation endpoint
            and/or alias.
    """
    raw_data = juju.cli("show-unit", unit_name)
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [v for v in data[unit_name]["relation-info"] if v["endpoint"] == relation_name]
    if not relation_data:
        raise ValueError(
            f"no relation data could be grabbed on relation with endpoint {relation_name}"
        )
    # Consider the case we are dealing with subordinate charms, e.g. grafana-agent
    # The field "relation-units" is structured slightly different.
    for idx in range(len(relation_data)):
        if target_unit_name in relation_data[idx]["related-units"]:
            break
    else:
        return None
    return (
        relation_data[idx]["related-units"].get(target_unit_name, {}).get("data", {}).get(key, {})
    )


def get_secret_by_label(juju: Juju, label: str) -> Dict[str, str]:
    for secret in juju.secrets():
        if label == secret.label:
            revealed_secret = juju.show_secret(secret.uri, reveal=True)
            return revealed_secret.content

    raise SecretNotFoundError(f"Secret with label {label} not found")


def get_certificate_from_unit(
    juju: Juju, unit: str, cert_type: TLSType, is_ca: bool = False
) -> str | None:
    """Retrieve a certificate from a unit."""
    command = f"cat {TLS_ROOT_DIR}/{cert_type.value}{'_ca' if is_ca else ''}.pem"
    output = juju.ssh(target=unit, command=command)
    if output.startswith("-----BEGIN CERTIFICATE-----"):
        return output

    return None


def set_password(
    juju: Juju,
    password: str,
    username: str = INTERNAL_USER,
    application: str = APP_NAME,
) -> None:
    """Set a user password (or update it if existing) via secret.

    Args:
        juju: An instance of Jubilant's Juju class on which to run Juju commands
        password: password to use
        username: the user to set the password
        application: the application the created secret will be granted to
    """
    secret_name = "system_users_secret"

    # if secret exists, update it, else add secret
    existing = next((s for s in juju.secrets() if s.name == secret_name), None)
    if existing:
        juju.update_secret(identifier=existing.uri, content={username: password})
        secret_id = existing.uri
    else:
        secret_id = juju.add_secret(name=secret_name, content={username: password})

    # grant the application access to this secret
    juju.grant_secret(identifier=secret_id, app=application)

    # update the application config to include the secret
    juju.config(app=application, values={INTERNAL_USER_PASSWORD_CONFIG: secret_id})


def download_client_certificate_from_unit(juju: Juju, app_name: str = APP_NAME) -> None:
    """Copy the client certificate files from a unit to the host's filesystem."""
    unit = next(iter(juju.status().get_units(app_name)))

    tls_path = TLS_ROOT_DIR

    for file in ["client.pem", "client.key", "client_ca.pem"]:
        juju.scp(f"{unit}:{tls_path}/{file}", file)


def get_storage_id(juju: Juju, unit_name: str, storage_name: str) -> str | None:
    """Retrieve the storage id associated with a unit."""
    storage_data = juju.cli("storage")
    # storage_data = storage_data.decode("utf-8")
    for line in storage_data.splitlines():
        # skip the header and irrelevant lines
        if not line or "Storage" in line or "detached" in line:
            continue

        if line.split()[0] == unit_name and line.split()[1].startswith(storage_name):
            return line.split()[1]

    return None


def get_user(
    endpoints: str,
    username: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> dict[str, Any] | None:
    """Get user details using `etcdctl`."""
    etcd_command = f"etcdctl user get {username} --endpoints={endpoints} -w json"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"

    try:
        result = subprocess.getoutput(etcd_command)
        logger.debug(f"User get result: {result}")
        return json.loads(result)["roles"]
    except json.JSONDecodeError:
        return None


def get_role(
    endpoints: str,
    rolename: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> list[dict[str, str]] | None:
    """Get role details using `etcdctl`."""
    etcd_command = f"etcdctl role get {rolename} --endpoints={endpoints} -w json"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"
    try:
        result = json.loads(subprocess.getoutput(etcd_command))["perm"]
        return [
            {
                "permType": perm["permType"],
                "key": base64.b64decode(perm["key"]).decode("utf-8"),
                "range_end": base64.b64decode(perm["range_end"]).decode("utf-8"),
            }
            for perm in result
        ]
    except json.JSONDecodeError:
        return None


def get_etcd_version(
    endpoint: str,
    user: str | None = None,
    password: str | None = None,
    tls_enabled: bool = False,
) -> str:
    """Check workload version of etcd endpoint."""
    etcd_command = f"etcdctl endpoint status --endpoints={endpoint} -w=json"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"

    try:
        result = subprocess.getoutput(etcd_command).split("\n")[0]
        status = json.loads(result)[0]
        return status["Status"]["version"]
    except KeyError:
        raise


def get_leader_unit_name(juju: Juju, app: str = APP_NAME) -> str:
    """Retrieve the leader unit's name.

    Raises:
        RuntimeError: if no leader unit is found.
    """
    for name, unit in juju.status().get_units(app).items():
        if unit.leader:
            return name

    raise RuntimeError(f"No leader unit found for app {app}")


def get_leader_unit_ip(juju: Juju, app: str = APP_NAME) -> str:
    """Retrieve the leader unit's public address.

    Raises:
        RuntimeError: if no leader unit is found.
    """
    for unit in juju.status().get_units(app).values():
        if unit.leader:
            return unit.public_address

    raise RuntimeError(f"No leader unit found for app {app}")


@contextlib.contextmanager
def fast_forward(juju: Juju, interval: int = 10):
    """Context manager that temporarily speeds up update-status hooks.

    Args:
        juju: An instance of Jubilant's Juju class on which to run Juju commands
        interval: How frequently (in seconds) to fire the update-status hook. Default value is 10.
    """
    old = juju.model_config()["update-status-hook-interval"]
    juju.model_config({"update-status-hook-interval": f"{interval}s"})
    try:
        yield
    finally:
        juju.model_config({"update-status-hook-interval": old})
