#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import subprocess
from pathlib import Path

import yaml
from pytest_operator.plugin import OpsTest

from literals import CLIENT_PORT

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def put_key(model: str, unit: str, endpoints: str, key: str, value: str) -> str:
    """Write data to etcd using `etcdctl` via `juju ssh`."""
    etcd_command = f"etcdctl put {key} {value} --endpoints={endpoints}"
    juju_command = f"juju ssh --model={model} {unit} {etcd_command}"

    return subprocess.getoutput(juju_command).split("\n")[0]


def get_key(model: str, unit: str, endpoints: str, key: str) -> str:
    """Read data from etcd using `etcdctl` via `juju ssh`."""
    etcd_command = f"etcdctl get {key} --endpoints={endpoints}"
    juju_command = f"juju ssh --model={model} {unit} {etcd_command}"

    return subprocess.getoutput(juju_command).split("\n")[1]


def get_cluster_members(model: str, unit: str, endpoints: str) -> list[dict]:
    """Query all cluster members from etcd using `etcdctl` via `juju ssh`."""
    etcd_command = f"etcdctl member list --endpoints={endpoints} -w=json"
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
