#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import subprocess

from pytest_operator.plugin import OpsTest

from literals import SNAP_NAME

logger = logging.getLogger(__name__)


async def existing_app(ops_test: OpsTest) -> str | None:
    """Return the name of an existing etcd cluster."""
    apps = json.loads(
        subprocess.check_output(
            f"juju status --model {ops_test.model.info.name} --format=json".split()
        )
    )["applications"]

    etcd_apps = {name: desc for name, desc in apps.items() if desc["charm-name"] == "charmed-etcd"}

    return list(etcd_apps.keys())[0] if etcd_apps else None


def start_continuous_writes(
    ops_test: OpsTest, app_name: str, endpoints: str, user: str, password: str
) -> None:
    model = ops_test.model_full_name
    # this is the unit where the `etcdctl` command is executed
    # it does not mean that data is written to this cluster member
    # before removing the unit used for running `etcdctl`, continuous writes should be stopped
    unit = ops_test.model.applications[app_name].units[0].name
    subprocess.Popen(
        [
            "python3",
            "tests/integration/ha/continuous_writes.py",
            model,
            unit,
            endpoints,
            user,
            password,
        ]
    )


def stop_continuous_writes() -> None:
    proc = subprocess.Popen(["pkill", "-9", "-f", "continuous_writes.py"])
    proc.communicate()


def count_writes(
    ops_test: OpsTest, app_name: str, endpoints: str, user: str, password: str
) -> int:
    model = ops_test.model_full_name
    unit = ops_test.model.applications[app_name].units[0].name
    key = "cw_key"

    etcd_command = f"""{SNAP_NAME}.etcdctl \
                            get {key} \
                            --endpoints={endpoints} \
                            --user={user} \
                            --password={password}
                            """
    juju_command = f"juju ssh --model={model} {unit} {etcd_command}"

    try:
        return int(subprocess.getoutput(juju_command).split("\n")[1])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(e)
