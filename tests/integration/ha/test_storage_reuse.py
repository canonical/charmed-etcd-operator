#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import APP_NAME, CHARM_PATH, get_storage_id
from .helpers import existing_app

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Deploy the charm with storage volume for data, allowing for skipping if already deployed."""
    # it is possible for users to provide their own cluster for HA testing.
    if await existing_app(ops_test):
        return

    # create storage to be used in this test
    # this assumes the test is run on a lxd cloud
    await ops_test.model.create_storage_pool("etcd-pool", "lxd")
    storage = {"data": {"pool": "etcd-pool", "size": 2048}}

    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS, storage=storage)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    assert len(ops_test.model.applications[APP_NAME].units) == NUM_UNITS

@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_attach_storage_after_scale_down(ops_test: OpsTest) -> None:
    """Make sure a unit is removed from the etcd cluster without downtime."""
    app = (await existing_app(ops_test)) or APP_NAME
    unit = ops_test.model.applications[app].units[-1]
    storage_id = get_storage_id(ops_test, unit.name, "data")

    # create a testfile to check if data in storage is persistent
    testfile = "/var/snap/charmed-etcd/common/var/lib/etcd/testfile"
    create_testfile_cmd = f"juju ssh {unit.name} --model={ops_test.model.info.name} -q sudo touch {testfile}"
    subprocess.run(create_testfile_cmd, shell=True)

    # remove the unit
    await ops_test.model.applications[app].destroy_unit(unit.name)
    await ops_test.model.wait_for_idle(
        apps=[app],
        status="active",
        wait_for_exact_units=NUM_UNITS - 1,
        wait_for_active=True,
        # if the cluster member cannot be removed immediately, the `storage_detaching` hook might fail temporarily
        # raise_on_error=False,
        timeout=1000,
    )

    # add unit with previous storage attached
    add_unit_cmd = f"add-unit {app} --model={ops_test.model.info.name} --attach-storage={storage_id}"
    return_code, _, _ = await ops_test.juju(*add_unit_cmd.split())
    assert return_code == 0, f"Failed to add unit with storage {storage_id}"

    new_unit = ops_test.model.applications[app].units[-1]
    await ops_test.model.wait_for_idle(
        apps=[app],
        status="active",
        wait_for_exact_units=NUM_UNITS,
        wait_for_active=True,
        # if the cluster member cannot be removed immediately, the `storage_detaching` hook might fail temporarily
        # raise_on_error=False,
        timeout=1000,
    )

    # check if the testfile is still there
    check_testfile_cmd = f"juju ssh {new_unit.name} --model={ops_test.model.info.name} -q sudo ls {testfile}"
    assert testfile == subprocess.getoutput(check_testfile_cmd)
