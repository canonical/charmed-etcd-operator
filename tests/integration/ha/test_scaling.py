#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION

from ..helpers import (
    APP_NAME,
    CHARM_PATH,
    get_cluster_endpoints,
    get_cluster_members,
    get_juju_leader_unit_name,
    get_raft_leader,
    get_secret_by_label,
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


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy the charm, allowing for skipping if already deployed."""
    # it is possible for users to provide their own cluster for HA testing.
    if await existing_app(ops_test):
        return

    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(CHARM_PATH, num_units=1)
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000)

    assert len(ops_test.model.applications[APP_NAME].units) == 1


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_scale_up(ops_test: OpsTest) -> None:
    """Make sure new units are added to the etcd cluster without downtime."""
    app = (await existing_app(ops_test)) or APP_NAME
    init_units_count = len(ops_test.model.applications[app].units)
    init_endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    # scale up
    await ops_test.model.applications[app].add_unit(count=2)
    await wait_until(
        ops_test, apps=[app], wait_for_exact_units=init_units_count + 2, idle_period=60
    )
    num_units = len(ops_test.model.applications[app].units)
    assert num_units == init_units_count + 2, (
        f"Expected {init_units_count + 2} units, got {num_units}."
    )

    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(ops_test, app)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == init_units_count + 2, (
        f"Expected {init_units_count + 2} cluster members, got {len(cluster_members)}."
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_scale_down(ops_test: OpsTest) -> None:
    """Make sure a unit is removed from the etcd cluster without downtime."""
    app = (await existing_app(ops_test)) or APP_NAME
    init_units_count = len(ops_test.model.applications[app].units)
    init_endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    # scale down
    unit = ops_test.model.applications[app].units[-1]
    await ops_test.model.applications[app].destroy_unit(unit.name)
    await wait_until(
        ops_test, apps=[app], wait_for_exact_units=init_units_count - 1, idle_period=60
    )
    num_units = len(ops_test.model.applications[app].units)
    assert num_units == init_units_count - 1, (
        f"Expected {init_units_count - 1} units, got {num_units}."
    )

    # check if unit has been removed from etcd cluster
    endpoints = get_cluster_endpoints(ops_test, app)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == init_units_count - 1, (
        f"Expected {init_units_count - 1} cluster members, got {len(cluster_members)}."
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_remove_raft_leader(ops_test: OpsTest) -> None:
    """Make sure the etcd cluster is still available when the Raft leader is removed."""
    app = (await existing_app(ops_test)) or APP_NAME
    init_endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    await ops_test.model.applications[app].add_unit(count=1)
    init_units_count = 3
    await wait_until(ops_test, apps=[app], wait_for_exact_units=init_units_count, idle_period=60)

    # check cluster membership after scaling up
    updated_endpoints = get_cluster_endpoints(ops_test, app)
    cluster_members = get_cluster_members(updated_endpoints)
    assert len(cluster_members) == init_units_count, (
        f"Expected {init_units_count} cluster members, got {len(cluster_members)}."
    )

    # find and remove the unit that is the current Raft leader
    init_raft_leader = get_raft_leader(endpoints=init_endpoints)
    await ops_test.model.applications[app].destroy_unit(init_raft_leader.replace(app, f"{app}/"))

    await wait_until(
        ops_test, apps=[app], wait_for_exact_units=init_units_count - 1, idle_period=60
    )
    num_units = len(ops_test.model.applications[app].units)
    assert num_units == init_units_count - 1, (
        f"Expected {init_units_count - 1} units, got {num_units}."
    )

    # check if unit has been removed from etcd cluster
    updated_endpoints = get_cluster_endpoints(ops_test, app)

    cluster_members = get_cluster_members(updated_endpoints)
    assert len(cluster_members) == init_units_count - 1, (
        f"Expected {init_units_count - 1} cluster members, got {len(cluster_members)}."
    )

    # check that another unit is now the Raft leader
    new_raft_leader = get_raft_leader(endpoints=updated_endpoints)
    assert new_raft_leader != init_raft_leader

    assert_continuous_writes_increasing(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_remove_multiple_units(ops_test: OpsTest) -> None:
    """Make sure multiple units can be removed from the etcd cluster without downtime."""
    app = (await existing_app(ops_test)) or APP_NAME
    init_endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    await ops_test.model.applications[app].add_unit(count=1)
    await wait_until(ops_test, apps=[app], wait_for_exact_units=3, idle_period=60)

    # remove all units except one
    for unit in ops_test.model.applications[app].units[1:]:
        await ops_test.model.applications[app].destroy_unit(unit.name)

    await wait_until(ops_test, apps=[app], wait_for_exact_units=1)

    num_units = len(ops_test.model.applications[app].units)
    assert num_units == 1, f"Expected 1 unit, got {num_units}."

    # check if unit has been removed from etcd cluster
    endpoints = get_cluster_endpoints(ops_test, app)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == 1, f"Expected 1 cluster member, got {len(cluster_members)}."

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_scale_to_zero_and_back(ops_test: OpsTest) -> None:
    """Make sure that removing all units and then adding them again works."""
    app = (await existing_app(ops_test)) or APP_NAME
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # remove all remaining units
    for unit in ops_test.model.applications[app].units:
        await ops_test.model.applications[app].destroy_unit(unit.name)

    # TODO fix wait_until to support this case
    await ops_test.model.wait_for_idle(
        apps=[app],
        wait_for_exact_units=0,
        # if the cluster member cannot be removed immediately, the `storage_detaching` hook might fail temporarily
        raise_on_error=False,
        timeout=1000,
    )

    # scale up again
    await ops_test.model.applications[app].add_unit(count=3)

    await wait_until(ops_test, apps=[app], wait_for_exact_units=3, idle_period=60)

    endpoints = get_cluster_endpoints(ops_test, app)
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
    # give time to write at least some data
    time.sleep(10)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == 3, f"Expected 3 cluster members, got {len(cluster_members)}."

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_remove_juju_leader(ops_test: OpsTest) -> None:
    """Make sure that removing the juju leader unit works."""
    app = (await existing_app(ops_test)) or APP_NAME
    init_units_count = len(ops_test.model.applications[app].units)
    init_endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    # scale down
    juju_leader_unit = await get_juju_leader_unit_name(ops_test, app)
    await ops_test.model.applications[app].destroy_unit(juju_leader_unit)

    await wait_until(ops_test, apps=[app], wait_for_exact_units=init_units_count - 1)
    num_units = len(ops_test.model.applications[app].units)
    assert num_units == init_units_count - 1, (
        f"Expected {init_units_count - 1} units, got {num_units}."
    )

    # check if unit has been removed from etcd cluster
    endpoints = get_cluster_endpoints(ops_test, app)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == init_units_count - 1, (
        f"Expected {init_units_count - 1} cluster members, got {len(cluster_members)}."
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_remove_application(ops_test: OpsTest) -> None:
    """Make sure removing the application works."""
    app = (await existing_app(ops_test)) or APP_NAME

    await ops_test.model.remove_application(app, block_until_done=True)
