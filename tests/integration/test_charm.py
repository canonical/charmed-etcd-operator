#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER

from .helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_cluster_members,
    get_juju_leader_unit_name,
    get_key,
    get_user_password,
    put_key,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """
    # Build and deploy charm from local source folder
    etcd_charm = await ops_test.build_charm(".")
    model = ops_test.model_full_name

    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(etcd_charm, num_units=NUM_UNITS)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)

    cluster_members = get_cluster_members(model, leader_unit, endpoints)
    assert len(cluster_members) == NUM_UNITS

    # make sure data can be written to the cluster
    password = await get_user_password(ops_test, user=INTERNAL_USER, unit=leader_unit)
    test_key = "test_key"
    test_value = "42"
    assert (
        put_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=test_key,
            value=test_value,
        )
        == "OK"
    )
    assert (
        get_key(model, leader_unit, endpoints, user=INTERNAL_USER, password=password, key=test_key)
        == test_value
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_authentication(ops_test: OpsTest) -> None:
    """Assert authentication is enabled by default.

    Test reading and writing data without providing credentials.
    Test updating the password of the internal admin user and make sure it can be used.
    """
    model = ops_test.model_full_name
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    test_key = "test_key"
    test_value = "42"
    new_password = "my_new_pwd"

    # check that reading/writing data without credentials fails
    assert get_key(model, leader_unit, endpoints, key=test_key) != test_value
    assert put_key(model, leader_unit, endpoints, key=test_key, value=test_value) != "OK"

    # run set-password action
    action = await ops_test.model.units.get(leader_unit).run_action(
        action_name="set-password", password=new_password
    )
    result = await action.wait()
    assert result.results.get(f"{INTERNAL_USER}-password") == new_password

    # run get-password action
    updated_password = await get_user_password(ops_test, user=INTERNAL_USER, unit=leader_unit)
    assert updated_password == new_password

    # use updated password to read data
    assert (
        get_key(
            model, leader_unit, endpoints, user=INTERNAL_USER, password=new_password, key=test_key
        )
        == test_value
    )
