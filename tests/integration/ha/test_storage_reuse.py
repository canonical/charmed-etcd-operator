#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, INTERNAL_USER_PASSWORD_CONFIG, PEER_RELATION

from ..helpers import (
    APP_NAME,
    CHARM_PATH,
    get_cluster_endpoints,
    get_cluster_members,
    get_secret_by_label,
    get_storage_id,
    get_unit_endpoint,
    put_key,
)
from ..helpers_deployment import wait_until
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    existing_app,
    start_continuous_writes,
    stop_continuous_writes,
)

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
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000)

    assert len(ops_test.model.applications[APP_NAME].units) == NUM_UNITS


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_attach_storage_after_scale_down(ops_test: OpsTest) -> None:
    """Make sure storage can be re-attached after removing a unit."""
    app = (await existing_app(ops_test)) or APP_NAME
    unit = ops_test.model.applications[app].units[-1]
    storage_id = get_storage_id(ops_test, unit.name, "data")
    init_endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    # remove the unit
    await ops_test.model.applications[app].destroy_unit(unit.name)
    await wait_until(ops_test, apps=[app], wait_for_exact_units=NUM_UNITS - 1, idle_period=60)

    # add unit with previous storage attached
    add_unit_cmd = (
        f"add-unit {app} --model={ops_test.model.info.name} --attach-storage={storage_id}"
    )
    return_code, _, _ = await ops_test.juju(*add_unit_cmd.split())
    assert return_code == 0, f"Failed to add unit with storage {storage_id}"

    new_unit = ops_test.model.applications[app].units[-1]
    await wait_until(ops_test, apps=[app], wait_for_exact_units=NUM_UNITS, idle_period=60)

    # ensure data can be written on the new unit
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=new_unit.name, app_name=app)
    assert (
        put_key(
            unit_endpoint,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    logger.info(f"{new_unit.name} is available again.")

    # check cluster formation after unit with existing storage was added
    updated_endpoints = get_cluster_endpoints(ops_test, app)
    cluster_members = get_cluster_members(updated_endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    assert_continuous_writes_increasing(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_attach_storage_after_scale_to_zero(ops_test: OpsTest) -> None:
    """Make sure storage can be re-attached after removing all units."""
    app = (await existing_app(ops_test)) or APP_NAME
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # remove all units except one - we need to know which storage to attach when scaling up again
    for unit in ops_test.model.applications[app].units[1:]:
        await ops_test.model.applications[app].destroy_unit(unit.name)

    await wait_until(ops_test, apps=[app], wait_for_exact_units=1, idle_period=60)

    # remove the remaining unit after saving the storage id
    unit = ops_test.model.applications[app].units[0]
    storage_id = get_storage_id(ops_test, unit.name, "data")
    await ops_test.model.applications[app].destroy_unit(unit.name)

    # `wait_until` doesn't work well with 0 units
    await ops_test.model.wait_for_idle(
        apps=[app],
        wait_for_exact_units=0,
        # if the cluster member cannot be removed immediately, the `storage_detaching` hook might fail temporarily
        raise_on_error=False,
        timeout=1000,
    )

    # scale up again re-attaching the storage
    add_unit_cmd = (
        f"add-unit {app} --model={ops_test.model.info.name} --attach-storage={storage_id}"
    )
    return_code, _, _ = await ops_test.juju(*add_unit_cmd.split())
    assert return_code == 0, f"Failed to add unit with storage {storage_id}"

    await wait_until(ops_test, apps=[app], wait_for_exact_units=1, idle_period=60)

    # ensure the newly added unit is functional
    new_unit = ops_test.model.applications[app].units[-1]
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=new_unit.name, app_name=app)
    assert (
        put_key(
            unit_endpoint,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    logger.info(f"{new_unit.name} is available again.")

    # start writing data to the cluster
    start_continuous_writes(endpoints=unit_endpoint, user=INTERNAL_USER, password=password)

    # scale up
    await ops_test.model.applications[app].add_unit(count=2)
    await wait_until(ops_test, apps=[app], wait_for_exact_units=NUM_UNITS, idle_period=60)

    # check cluster formation after new cluster was forced
    endpoints = get_cluster_endpoints(ops_test, app)
    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_attach_storage_after_removing_application(ops_test: OpsTest) -> None:
    """Make sure storage can be re-attached to a completely new etcd application."""
    app = (await existing_app(ops_test)) or APP_NAME
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # remove all units except one - we need to know which storage to attach when scaling up again
    for unit in ops_test.model.applications[app].units[1:]:
        await ops_test.model.applications[app].destroy_unit(unit.name)

    await wait_until(ops_test, apps=[app], wait_for_exact_units=1, idle_period=60)

    # remove the remaining unit after saving the storage id
    unit = ops_test.model.applications[app].units[0]
    storage_id = get_storage_id(ops_test, unit.name, "data")

    # remove the entire application
    await ops_test.model.remove_application(app, block_until_done=True)

    # we are going to deploy a new cluster, but with an existing database
    # that means we need to configure the correct admin password in advance
    admin_secret = "root_password"
    secret_id = await ops_test.model.add_secret(
        name=admin_secret, data_args=[f"{INTERNAL_USER}={password}"]
    )

    # deploy new cluster, attaching the storage from the previous last unit to the new first unit
    deploy_cluster_with_storage_cmd = f"""deploy {CHARM_PATH} \
        --model={ops_test.model.info.name} \
        --attach-storage={storage_id} \
        --config {INTERNAL_USER_PASSWORD_CONFIG}={secret_id}
        """

    return_code, _, _ = await ops_test.juju(*deploy_cluster_with_storage_cmd.split())
    assert return_code == 0, f"Failed to deploy app with storage {storage_id}"
    await ops_test.model.grant_secret(secret_name=admin_secret, application=APP_NAME)

    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=1, idle_period=60)

    # make sure the new application/etcd cluster is available
    new_unit = ops_test.model.applications[APP_NAME].units[-1]
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=new_unit.name, app_name=APP_NAME)
    assert (
        put_key(
            unit_endpoint,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    logger.info(f"{new_unit.name} is available again.")

    # start writing data to the new cluster
    start_continuous_writes(endpoints=unit_endpoint, user=INTERNAL_USER, password=password)

    # scale up
    await ops_test.model.applications[APP_NAME].add_unit(count=2)
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS, idle_period=60)

    # check cluster formation
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
