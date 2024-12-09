#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION

from .helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_cluster_members,
    get_juju_leader_unit_name,
    get_key,
    get_secret_by_label,
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
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

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
    """Assert authentication is enabled by default."""
    model = ops_test.model_full_name
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    test_key = "test_key"
    test_value = "42"

    # check that reading/writing data without credentials fails
    assert get_key(model, leader_unit, endpoints, key=test_key) != test_value
    assert put_key(model, leader_unit, endpoints, key=test_key, value=test_value) != "OK"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_update_admin_password(ops_test: OpsTest) -> None:
    """Assert the admin password is updated when adding a user secret to the config."""
    model = ops_test.model_full_name
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    test_key = "test_key"
    test_value = "42"

    # create a user secret and grant it to the application
    secret_name = "test_secret"
    new_password = "some-password"

    secret_id = await ops_test.model.add_secret(
        name=secret_name, data_args=[f"admin-password={new_password}"]
    )
    await ops_test.model.grant_secret(secret_name=secret_name, application=APP_NAME)

    # update the application config to include the secret
    await ops_test.model.applications[APP_NAME].set_config({"admin-password": secret_id})
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # perform read operation with the updated password
    assert (
        get_key(
            model, leader_unit, endpoints, user=INTERNAL_USER, password=new_password, key=test_key
        )
        == test_value
    )

    # update the config again and remove the option `admin-password`
    await ops_test.model.applications[APP_NAME].reset_config(["admin-password"])
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # make sure we can still read data with the previously set password
    assert (
        get_key(
            model, leader_unit, endpoints, user=INTERNAL_USER, password=new_password, key=test_key
        )
        == test_value
    )
