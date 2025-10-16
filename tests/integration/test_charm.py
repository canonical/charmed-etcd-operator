#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, INTERNAL_USER_PASSWORD_CONFIG, PEER_RELATION
from statuses import CharmStatuses

from .helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_cluster_members,
    get_key,
    get_secret_by_label,
    put_key,
    set_password,
)
from .helpers_deployment import wait_until

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(charm: str, ops_test: OpsTest) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """
    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(charm, num_units=NUM_UNITS)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == NUM_UNITS

    # make sure data can be written to the cluster
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


@pytest.mark.abort_on_fail
async def test_authentication(ops_test: OpsTest) -> None:
    """Assert authentication is enabled by default."""
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)

    # check that reading/writing data without credentials fails
    assert get_key(endpoints, key=TEST_KEY) != TEST_VALUE
    assert put_key(endpoints, key=TEST_KEY, value=TEST_VALUE) != "OK"


@pytest.mark.abort_on_fail
async def test_update_admin_password(ops_test: OpsTest) -> None:
    """Assert the admin password is updated when adding a user secret to the config."""
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)

    # create a user secret and grant it to the application
    new_password = "some-password"
    await set_password(ops_test, new_password)
    await wait_until(ops_test, apps=[APP_NAME])

    # perform read operation with the updated password
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=new_password, key=TEST_KEY) == TEST_VALUE
    )

    # update the config again and remove the option `admin-password`
    await ops_test.model.applications[APP_NAME].reset_config([INTERNAL_USER_PASSWORD_CONFIG])
    await wait_until(ops_test, apps=[APP_NAME])

    # make sure we can still read data with the previously set password
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=new_password, key=TEST_KEY) == TEST_VALUE
    )


@pytest.mark.abort_on_fail
async def test_user_secret_permissions(ops_test: OpsTest) -> None:
    """If a user secret is not granted, ensure we can process updated permissions."""
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)

    logger.info("Creating new user secret")
    secret_name = "my_secret"
    new_password = "even-newer-password"
    secret_id = await ops_test.model.add_secret(
        name=secret_name, data_args=[f"{INTERNAL_USER}={new_password}"]
    )

    logger.info("Updating configuration with the new secret - but without access")
    await ops_test.model.applications[APP_NAME].set_config(
        {INTERNAL_USER_PASSWORD_CONFIG: secret_id}
    )

    await wait_until(
        ops_test,
        apps=[APP_NAME],
        apps_full_statuses={APP_NAME: [CharmStatuses.SECRET_ACCESS_ERROR.value]},
    )

    logger.info("Secret access will be granted now - wait for updated password")
    # deferred `config_changed` event will be retried before `update_status`
    async with ops_test.fast_forward("10s"):
        await ops_test.model.grant_secret(secret_name=secret_name, application=APP_NAME)

    await wait_until(ops_test, apps=[APP_NAME])

    # perform read operation with the updated password
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=new_password, key=TEST_KEY) == TEST_VALUE
    ), "password update failed"

    logger.info("Password update successful after secret was granted")
