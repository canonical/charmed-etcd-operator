#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import subprocess
from pathlib import Path

import yaml
from pytest_operator.plugin import OpsTest

from literals import CLIENT_PORT

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def put_key(endpoints: str, key: str, value: str) -> str:
    """Write data to etcd using `etcdctl`."""
    return subprocess.run(
        args=[
            "etcdctl",
            "put",
            key,
            value,
            f"--endpoints={endpoints}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\n")[0]


def get_key(endpoints: str, key: str) -> str:
    """Read data from etcd using `etcdctl`."""
    return subprocess.run(
        args=[
            "etcdctl",
            "get",
            key,
            f"--endpoints={endpoints}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\n")[1]


def get_cluster_members(endpoints: str) -> list[dict]:
    """Query all cluster members from etcd using `etcdctl`."""
    result = subprocess.run(
        args=[
            "etcdctl",
            "member",
            "list",
            f"--endpoints={endpoints}",
            "-w=json",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    return json.loads(result)["members"]


def get_cluster_endpoints(ops_test: OpsTest, app_name: str = APP_NAME) -> str:
    """Resolve the etcd endpoints for a given juju application."""
    return ",".join(
        [
            f"http://{unit.public_address}:{(CLIENT_PORT)}"
            for unit in ops_test.model.applications[app_name].units
        ]
    )
