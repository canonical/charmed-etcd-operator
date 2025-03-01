#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
import json
import logging
import subprocess
import time
from typing import Tuple

from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_attempt, wait_fixed

from literals import SNAP_NAME, SNAP_REVISION

logger = logging.getLogger(__name__)

WRITES_LAST_WRITTEN_VAL_PATH = "last_written_value"
ETCD_PROCESS = f"/snap/{SNAP_NAME}/{SNAP_REVISION}/bin/etcd"
ETCD_SERVICE_PATH = "/etc/systemd/system/snap.charmed-etcd.etcd.service"


async def existing_app(ops_test: OpsTest) -> str | None:
    """Return the name of an existing etcd cluster.

    Returns:
        str | None: name of an application deployment for `charmed-etcd`
    """
    apps = json.loads(
        subprocess.check_output(
            f"juju status --model {ops_test.model.info.name} --format=json".split()
        )
    )["applications"]

    etcd_apps = {name: desc for name, desc in apps.items() if desc["charm-name"] == "charmed-etcd"}

    return list(etcd_apps.keys())[0] if etcd_apps else None


def start_continuous_writes(endpoints: str, user: str, password: str) -> None:
    """Create a subprocess instance of `continuous writes` and start writing data to etcd."""
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
    """Shut down the subprocess instance of the `continuous writes`."""
    proc = subprocess.Popen(["pkill", "-15", "-f", "continuous_writes.py"])
    proc.communicate()


def count_writes(endpoints: str, user: str, password: str) -> Tuple[int, int]:
    """Get the current value of the `continuous writes`.

    Returns:
        int: the current value of the key named `cw_key`
        int: the revision number of the key named `cw_key`
    """
    key = "cw_key"

    etcd_command = f"""etcdctl \
                    get {key} \
                    --endpoints={endpoints} \
                    --user={user} \
                    --password={password} \
                    --write-out='json'
                    """

    try:
        result = subprocess.getoutput(etcd_command).split("\n")
        result = json.loads(result[0])
        return (
            int(base64.b64decode(result["kvs"][0]["value"]).decode("utf-8")),
            result["kvs"][0]["version"],
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(e)


def assert_continuous_writes_increasing(endpoints: str, user: str, password: str) -> None:
    """Assert that the continuous writes are increasing."""
    writes_count, _ = count_writes(endpoints, user, password)
    time.sleep(10)
    more_writes, _ = count_writes(endpoints, user, password)
    assert more_writes > writes_count, "Writes not continuing to DB"
    logger.info("Continuous writes are increasing.")


def assert_continuous_writes_consistent(
    endpoints: str, user: str, password: str, ignore_revision: bool = False
) -> None:
    """Assert that the continuous writes are consistent."""
    for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(5)):
        with attempt:
            with open(WRITES_LAST_WRITTEN_VAL_PATH, "r") as f:
                last_written_value = int(f.read().rstrip())

    for endpoint in endpoints.split(","):
        last_etcd_value, last_etcd_revision = count_writes(endpoint, user, password)
        if ignore_revision:
            assert last_written_value == last_etcd_value, (
                f"endpoint: {endpoint}, expected value: {last_written_value}, current value: {last_etcd_value}"
            )
        else:
            assert last_written_value == last_etcd_value == last_etcd_revision, (
                f"endpoint: {endpoint}, expected value: {last_written_value}, current value: {last_etcd_value}, revision: {last_etcd_revision}."
            )
        logger.info(f"Continuous writes are consistent on {endpoint}.")


def send_process_control_signal(unit_name: str, model_full_name: str, signal: str) -> None:
    """Send control signal to an etcd-process running on a Juju unit.

    Args:
        unit_name: the Juju unit running the process
        model_full_name: the Juju model for the unit
        signal: the signal to issue, e.g `SIGKILL`
    """
    juju_cmd = f"JUJU_MODEL={model_full_name} juju ssh {unit_name} sudo -i 'pkill --signal {signal} -f {ETCD_PROCESS}'"

    try:
        subprocess.check_output(
            juju_cmd, stderr=subprocess.PIPE, shell=True, universal_newlines=True, timeout=3
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    logger.info(f"Signal {signal} sent to etcd process on unit {unit_name}.")


async def patch_restart_delay(ops_test: OpsTest, unit_name: str, delay: int) -> None:
    """Update the restart delay in the snap's systemd service file."""
    add_delay_cmd = (
        f"exec --unit {unit_name} -- "
        f"sudo sed -i -e '/^[Service]/a RestartSec={delay}' "
        f"{ETCD_SERVICE_PATH}"
    )
    await ops_test.juju(*add_delay_cmd.split(), check=True)

    # reload the daemon for systemd to reflect changes
    reload_cmd = f"exec --unit {unit_name} -- sudo systemctl daemon-reload"
    await ops_test.juju(*reload_cmd.split(), check=True)
