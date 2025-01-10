#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import subprocess

from pytest_operator.plugin import OpsTest

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


def start_continuous_writes(endpoints: str, user: str, password: str) -> None:
    subprocess.Popen(
        [
            "python3",
            "tests/integration/ha/continuous_writes.py",
            endpoints,
            user,
            password,
        ]
    )


def stop_continuous_writes() -> None:
    proc = subprocess.Popen(["pkill", "-9", "-f", "continuous_writes.py"])
    proc.communicate()


def count_writes(endpoints: str, user: str, password: str) -> int:
    key = "cw_key"

    etcd_command = f"""etcdctl \
                    get {key} \
                    --endpoints={endpoints} \
                    --user={user} \
                    --password={password}
                    """

    try:
        return int(subprocess.getoutput(etcd_command).split("\n")[1])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(e)
