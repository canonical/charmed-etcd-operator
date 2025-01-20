#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import subprocess
import time

from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

WRITES_LAST_WRITTEN_VAL_PATH = "last_written_value"


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


def assert_continuous_writes_increasing(endpoints: str, user: str, password: str) -> None:
    """Assert that the continuous writes are increasing."""
    writes_count = count_writes(endpoints, user, password)
    time.sleep(10)
    more_writes = count_writes(endpoints, user, password)
    assert more_writes > writes_count, "Writes not continuing to DB"


def assert_continuous_writes_consistent(endpoints: str, user: str, password: str) -> None:
    """Assert that the continuous writes are consistent."""
    for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(5)):
        with attempt:
            with open(WRITES_LAST_WRITTEN_VAL_PATH, "r") as f:
                last_written_value = int(f.read().rstrip())

    for endpoint in endpoints.split(","):
        last_etcd_value = count_writes(endpoint, user, password)
        # when stopping the writes, it may happen that data was written to etcd but not to file yet
        assert (
            last_written_value <= last_etcd_value
        ), f"endpoint: {endpoint}, expected value: {last_written_value}, last value in endpoint: {last_etcd_value}."
