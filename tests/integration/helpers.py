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

from literals import CLIENT_PORT, SNAP_NAME

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def put_key(
    model: str,
    unit: str,
    endpoints: str,
    key: str,
    value: str,
    user: str | None = None,
    password: str | None = None,
) -> str:
    """Write data to etcd using `etcdctl` via `juju ssh`."""
    etcd_command = f"{SNAP_NAME}.etcdctl put {key} {value} --endpoints={endpoints}"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"
    juju_command = f"juju ssh --model={model} {unit} {etcd_command}"

    return subprocess.getoutput(juju_command).split("\n")[0]


def get_key(
    model: str,
    unit: str,
    endpoints: str,
    key: str,
    user: str | None = None,
    password: str | None = None,
) -> str:
    """Read data from etcd using `etcdctl` via `juju ssh`."""
    etcd_command = f"{SNAP_NAME}.etcdctl get {key} --endpoints={endpoints}"
    if user:
        etcd_command = f"{etcd_command} --user={user}"
    if password:
        etcd_command = f"{etcd_command} --password={password}"

    juju_command = f"juju ssh --model={model} {unit} {etcd_command}"

    return subprocess.getoutput(juju_command).split("\n")[1]


def get_cluster_members(model: str, unit: str, endpoints: str) -> list[dict]:
    """Query all cluster members from etcd using `etcdctl` via `juju ssh`."""
    etcd_command = f"{SNAP_NAME}.etcdctl member list --endpoints={endpoints} -w=json"
    juju_command = f"juju ssh --model={model} {unit} {etcd_command}"

    result = subprocess.getoutput(juju_command).split("\n")[0]

    return json.loads(result)["members"]


def get_cluster_endpoints(ops_test: OpsTest, app_name: str = APP_NAME) -> str:
    """Resolve the etcd endpoints for a given juju application."""
    return ",".join(
        [
            f"http://{unit.public_address}:{CLIENT_PORT}"
            for unit in ops_test.model.applications[app_name].units
        ]
    )


async def get_juju_leader_unit_name(ops_test: OpsTest, app_name: str = APP_NAME) -> str:
    """Retrieve the leader unit name."""
    for unit in ops_test.model.applications[app_name].units:
        if await unit.is_leader_from_status():
            return unit.name


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
