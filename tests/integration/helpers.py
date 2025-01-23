#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict

import yaml
from pytest_operator.plugin import OpsTest

from literals import CLIENT_PORT, TLSType

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


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


async def get_juju_leader_unit_name(ops_test: OpsTest, app_name: str = APP_NAME) -> str:
    """Retrieve the leader unit name."""
    for unit in ops_test.model.applications[app_name].units:
        if await unit.is_leader_from_status():
            return unit.name
    raise Exception("No leader unit found")


async def get_secret_by_label(ops_test: OpsTest, label: str) -> Dict[str, str] | None:
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

    return None


def get_certificate_from_unit(model: str, unit: str, cert_type: TLSType) -> str | None:
    """Retrieve a certificate from a unit."""
    command = f'juju ssh --model={model} {unit} "cat /var/snap/charmed-etcd/common/tls/{cert_type.value}.pem"'
    output = subprocess.getoutput(command)
    if output.startswith("-----BEGIN CERTIFICATE-----"):
        return output

    return None


async def download_client_certificate_from_unit(
    ops_test: OpsTest, app_name: str = APP_NAME
) -> None:
    """Copy the client certificate files from a unit to the host's filesystem."""
    unit = ops_test.model.applications[app_name].units[0]
    tls_path = "/var/snap/charmed-etcd/common/tls"

    for file in ["client.pem", "client.key", "client_ca.pem"]:
        await unit.scp_from(f"{tls_path}/{file}", file)
