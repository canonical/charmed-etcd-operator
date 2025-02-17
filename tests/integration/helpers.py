#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Dict

import yaml
from pytest_operator.plugin import OpsTest

from literals import CLIENT_PORT, PEER_RELATION, TLSType

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME: str = METADATA["name"]
CHARM_PATH = "./charmed-etcd_ubuntu@24.04-amd64.charm"


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


def get_cluster_members(endpoints: str, tls_enabled: bool = False) -> list[dict]:
    """Query all cluster members from etcd using `etcdctl`."""
    etcd_command = f"etcdctl member list --endpoints={endpoints} -w=json"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
            --cacert client_ca.pem \
            --cert client.pem \
            --key client.key"

    result = subprocess.getoutput(etcd_command).split("\n")[0]

    return json.loads(result)["members"]


def get_cluster_endpoints(
    ops_test: OpsTest, app_name: str = APP_NAME, tls_enabled: bool = False
) -> str:
    """Resolve the etcd endpoints for a given juju application."""
    return ",".join(
        [
            f"{'https' if tls_enabled else 'http'}://{unit.public_address}:{CLIENT_PORT}"
            for unit in ops_test.model.applications[app_name].units
        ]
    )


def get_raft_leader(endpoints: str, tls_enabled: bool = False) -> str:
    """Query the Raft leader via the `endpoint status` and `member list` commands.

    Returns:
        str: the member-name of the Raft leader, e.g. `etcd42`
    """
    etcd_command = f"etcdctl endpoint status --endpoints={endpoints} -w=json"
    if tls_enabled:
        etcd_command = f"{etcd_command} \
                --cacert client_ca.pem \
                --cert client.pem \
                --key client.key"

    # query leader id
    result = subprocess.getoutput(etcd_command).split("\n")[0]
    members = json.loads(result)
    leader_id = members[0]["Status"]["leader"]

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


async def get_application_relation_data(
    ops_test: OpsTest, application_name: str, relation_name: str, key: str
) -> str | None:
    """Get relation data for an application.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        relation_name: name of the relation to get connection data from
        key: key of data to be retrieved
        relation_id: id of the relation to get connection data from

    Returns:
        the relation data that was requested, or None if no data in the relation

    Raises:
        ValueError if it's not possible to get application unit data
            or if there is no data for the particular relation endpoint.
    """
    unit_name = await get_juju_leader_unit_name(ops_test, application_name)
    raw_data = (await ops_test.juju("show-unit", unit_name))[1]
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for {unit_name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [v for v in data[unit_name]["relation-info"] if v["endpoint"] == relation_name]
    if len(relation_data) == 0:
        raise ValueError(
            f"no relation data could be grabbed on relation with endpoint {relation_name}"
        )
    return relation_data[0]["application-data"].get(key)


async def wait_for_cluster_formation(ops_test: OpsTest, app_name: str = APP_NAME):
    """Wait until all cluster members have been promoted to full-voting member."""
    try:
        if learner := await get_application_relation_data(
            ops_test, app_name, PEER_RELATION, "learning_member"
        ):
            while True:
                logger.info(f"Waiting for learning-member {learner}")
                time.sleep(5)
                # this will raise with `ValueError` if not found and thereby break the loop
                learner = await get_application_relation_data(
                    ops_test, app_name, PEER_RELATION, "learning_member"
                )
    except ValueError:
        pass


async def get_juju_leader_unit_name(ops_test: OpsTest, app_name: str = APP_NAME) -> str:
    """Retrieve the leader unit name."""
    for unit in ops_test.model.applications[app_name].units:
        if await unit.is_leader_from_status():
            return unit.name
    raise Exception("No leader unit found")


async def get_secret_by_label(ops_test: OpsTest, label: str) -> Dict[str, str]:
    secrets_raw = await ops_test.juju("list-secrets")
    secret_ids = [
        secret_line.split()[0] for secret_line in secrets_raw[1].split("\n")[1:] if secret_line
    ]

    for secret_id in secret_ids:
        secret_data_raw = await ops_test.juju(
            "show-secret", "--format", "json", "--reveal", secret_id
        )
        secret_data = json.loads(secret_data_raw[1])

        if label == secret_data[secret_id].get("label"):
            return secret_data[secret_id]["content"]["Data"]

    raise SecretNotFoundError(f"Secret with label {label} not found")


def get_certificate_from_unit(
    model: str, unit: str, cert_type: TLSType, is_ca: bool = False
) -> str | None:
    """Retrieve a certificate from a unit."""
    command = f'juju ssh --model={model} {unit} "cat /var/snap/charmed-etcd/common/tls/{cert_type.value}{"_ca" if is_ca else ""}.pem"'
    output = subprocess.getoutput(command)
    if output.startswith("-----BEGIN CERTIFICATE-----"):
        return output

    return None


async def add_secret(ops_test: OpsTest, secret_name: str, content: dict[str, str]) -> str:
    """Add a secret to the model.

    Args:
        ops_test (OpsTest): The current test harness.
        secret_name (str): The name of the secret.
        content (dict[str, str]): The content of the secret.

    Returns:
        str: The secret ID.
    """
    assert ops_test.model is not None, "Model is not set"
    return_code, std_out, std_err = await ops_test.juju(
        "add-secret", secret_name, " ".join([f"{key}={value}" for key, value in content.items()])
    )

    assert return_code == 0, f"Failed to add secret: {std_err}"
    logger.info(f"Added secret {secret_name} to the model")
    return std_out.strip()


async def download_client_certificate_from_unit(
    ops_test: OpsTest, app_name: str = APP_NAME
) -> None:
    """Copy the client certificate files from a unit to the host's filesystem."""
    unit = ops_test.model.applications[app_name].units[0]
    tls_path = "/var/snap/charmed-etcd/common/tls"

    for file in ["client.pem", "client.key", "client_ca.pem"]:
        await unit.scp_from(f"{tls_path}/{file}", file)


def get_storage_id(ops_test: OpsTest, unit_name: str, storage_name: str) -> str:
    """Retrieve the storage id associated with a unit."""
    model_name = ops_test.model.info.name

    storage_data = subprocess.check_output(f"juju storage --model={model_name}".split())
    storage_data = storage_data.decode("utf-8")
    for line in storage_data.splitlines():
        # skip the header and irrelevant lines
        if not line or "Storage" in line or "detached" in line:
            continue

        if line.split()[0] == unit_name and line.split()[1].startswith(storage_name):
            return line.split()[1]
