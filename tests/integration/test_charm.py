#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, INTERNAL_USER_PASSWORD_CONFIG, PEER_RELATION

from .helpers import (
    APP_NAME,
    CHARM_PATH,
    get_cluster_endpoints,
    get_cluster_members,
    get_key,
    get_secret_by_label,
    put_key,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"


@pytest.mark.runner(["self-hosted", "linux", "X64", "noble", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """
    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS

    # make sure data can be written to the cluster
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    assert (
        put_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    assert get_key(endpoints, user=INTERNAL_USER, password=password, key=TEST_KEY) == TEST_VALUE


@pytest.mark.runner(["self-hosted", "linux", "X64", "noble", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_authentication(ops_test: OpsTest) -> None:
    """Assert authentication is enabled by default."""
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)

    # check that reading/writing data without credentials fails
    assert get_key(endpoints, key=TEST_KEY) != TEST_VALUE
    assert put_key(endpoints, key=TEST_KEY, value=TEST_VALUE) != "OK"


@pytest.mark.runner(["self-hosted", "linux", "X64", "noble", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_update_admin_password(ops_test: OpsTest) -> None:
    """Assert the admin password is updated when adding a user secret to the config."""
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)

    # create a user secret and grant it to the application
    secret_name = "test_secret"
    new_password = "some-password"

    secret_id = await ops_test.model.add_secret(
        name=secret_name, data_args=[f"{INTERNAL_USER}={new_password}"]
    )
    await ops_test.model.grant_secret(secret_name=secret_name, application=APP_NAME)

    # update the application config to include the secret
    await ops_test.model.applications[APP_NAME].set_config(
        {INTERNAL_USER_PASSWORD_CONFIG: secret_id}
    )
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # perform read operation with the updated password
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=new_password, key=TEST_KEY) == TEST_VALUE
    )

    # update the config again and remove the option `admin-password`
    await ops_test.model.applications[APP_NAME].reset_config([INTERNAL_USER_PASSWORD_CONFIG])
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # make sure we can still read data with the previously set password
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=new_password, key=TEST_KEY) == TEST_VALUE
    )
