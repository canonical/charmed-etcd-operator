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
    get_raft_leader,
    get_remaining_endpoints,
    get_secret_by_label,
    get_unit_endpoint,
    is_endpoint_up,
)
from ..helpers_deployment import wait_until
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    existing_app,
    start_continuous_writes,
    stop_continuous_writes,
)
from .helpers_network import (
    cut_network_from_unit_with_ip_change,
    cut_network_from_unit_without_ip_change,
    get_controller_hostname,
    hostname_from_unit,
    ip_address_from_unit,
    is_unit_reachable,
    restore_network_for_unit_with_ip_change,
    restore_network_for_unit_without_ip_change,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy the charm, allowing for skipping if already deployed."""
    # it is possible for users to provide their own cluster for HA testing.
    if await existing_app(ops_test):
        return

    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS)
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000)


@pytest.mark.skip()
@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_network_cut_on_raft_leader_without_ip_change(ops_test: OpsTest) -> None:
    """Make sure the cluster can self-heal and the unit reconfigures after network disconnect."""
    app = (await existing_app(ops_test)) or APP_NAME

    # make sure we have at least two units so we can stop one of them
    if len(ops_test.model.applications[app].units) < 2:
        await ops_test.model.applications[app].add_unit(count=1)
        await wait_until(
            ops_test,
            apps=[app],
            apps_statuses=["active"],
            units_statuses=["active"],
            wait_for_exact_units=2,
        )

    init_units_count = len(ops_test.model.applications[app].units)
    endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
    time.sleep(10)

    # get details for the current raft leader in the cluster
    initial_raft_leader = get_raft_leader(endpoints=endpoints)
    logger.info(f"initial raft leader: {initial_raft_leader}")
    leader_unit = initial_raft_leader.replace(app, f"{app}/")

    # cut network from the current cluster/raft leader
    leader_hostname = await hostname_from_unit(ops_test, unit_name=leader_unit)
    cut_network_from_unit_without_ip_change(leader_hostname)

    # make sure the unit is not reachable from the other units
    for unit in ops_test.model.applications[app].units:
        hostname = await hostname_from_unit(ops_test, unit.name)
        assert not is_unit_reachable(hostname, leader_hostname)

    # make sure the unit is not reachable from the controller
    controller_hostname = await get_controller_hostname(ops_test)
    assert not is_unit_reachable(controller_hostname, leader_hostname)
    logger.info(f"{leader_unit} is not reachable via network.")

    # verify the cluster member is not up anymore
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
    assert not is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
    logger.info(f"etcd endpoint on {leader_unit} is not available.")

    # as the stopped member is unresponsive, only query the endpoints still available
    remaining_endpoints = get_remaining_endpoints(endpoints, unit_endpoint)

    # ensure a new leader was assigned after waiting for the `election timeout`
    new_raft_leader = get_raft_leader(endpoints=remaining_endpoints)
    logger.info(f"new raft leader: {new_raft_leader}")
    assert new_raft_leader != initial_raft_leader, (
        "raft leadership not transferred after network disconnect of the leader"
    )

    # ensure data is continuing to be written in the cluster
    assert_continuous_writes_increasing(
        endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
    )

    # reconnect the network for the disconnected unit
    restore_network_for_unit_without_ip_change(leader_hostname)
    logger.info(f"Network restored for {leader_unit}")

    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active"],
        units_statuses=["active"],
        wait_for_exact_units=init_units_count,
    )

    # ensure the member is up again
    assert is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
    logger.info(f"{leader_unit} is available again.")

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == init_units_count, (
        f"expected {init_units_count} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    # ensure data is written in the cluster
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
    # that's why we will miss writes here, and therefore ignore the revision of the key
    assert_continuous_writes_consistent(
        endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_network_cut_on_raft_leader_with_ip_change(ops_test: OpsTest) -> None:
    """Make sure the cluster can self-heal and the unit reconfigures after network disconnect."""
    app = (await existing_app(ops_test)) or APP_NAME

    # make sure we have at least two units so we can stop one of them
    if len(ops_test.model.applications[app].units) < 2:
        await ops_test.model.applications[app].add_unit(count=1)
        await wait_until(
            ops_test,
            apps=[app],
            apps_statuses=["active"],
            units_statuses=["active"],
            wait_for_exact_units=2,
        )

    init_units_count = len(ops_test.model.applications[app].units)
    endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
    time.sleep(10)

    # get details for the current raft leader in the cluster
    initial_raft_leader = get_raft_leader(endpoints=endpoints)
    logger.info(f"initial raft leader: {initial_raft_leader}")
    leader_unit = initial_raft_leader.replace(app, f"{app}/")

    # cut network from the current cluster/raft leader
    leader_hostname = await hostname_from_unit(ops_test, unit_name=leader_unit)
    leader_ip = await ip_address_from_unit(ops_test, unit_name=leader_unit)
    cut_network_from_unit_with_ip_change(leader_hostname)

    # make sure the unit is not reachable from the other units
    for unit in ops_test.model.applications[app].units:
        hostname = await hostname_from_unit(ops_test, unit.name)
        assert not is_unit_reachable(hostname, leader_hostname)

    # make sure the unit is not reachable from the controller
    controller_hostname = await get_controller_hostname(ops_test)
    assert not is_unit_reachable(controller_hostname, leader_hostname)
    logger.info(f"{leader_unit} is not reachable via network.")

    # verify the cluster member is not up anymore
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
    assert not is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
    logger.info(f"etcd endpoint on {leader_unit} is not available.")

    # as the stopped member is unresponsive, only query the endpoints still available
    remaining_endpoints = get_remaining_endpoints(endpoints, unit_endpoint)

    # ensure a new leader was assigned after waiting for the `election timeout`
    new_raft_leader = get_raft_leader(endpoints=remaining_endpoints)
    logger.info(f"new raft leader: {new_raft_leader}")
    assert new_raft_leader != initial_raft_leader, (
        "raft leadership not transferred after network disconnect of the leader"
    )

    # ensure data is continuing to be written in the cluster
    assert_continuous_writes_increasing(
        endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
    )

    # reconnect the network for the disconnected unit
    restore_network_for_unit_with_ip_change(leader_hostname)
    logger.info(f"Network has been restored for {leader_unit}")

    await wait_until(
        ops_test,
        apps=[app],
        apps_statuses=["active"],
        units_statuses=["active"],
        wait_for_exact_units=init_units_count,
    )

    # ensure the member is up again
    new_unit_ip = await ip_address_from_unit(ops_test, unit_name=leader_unit)
    unit_endpoint_updated = unit_endpoint.replace(leader_ip, new_unit_ip)
    assert is_endpoint_up(unit_endpoint_updated, user=INTERNAL_USER, password=password)
    logger.info(f"{leader_unit} is available again with new ip {new_unit_ip}")

    endpoints_updated = endpoints.replace(leader_ip, new_unit_ip)
    cluster_members = get_cluster_members(endpoints_updated)
    assert len(cluster_members) == init_units_count, (
        f"expected {init_units_count} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    # ensure data is written in the cluster
    assert_continuous_writes_increasing(
        endpoints=endpoints_updated, user=INTERNAL_USER, password=password
    )
    stop_continuous_writes()
    # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
    # that's why we will miss writes here, and therefore ignore the revision of the key
    assert_continuous_writes_consistent(
        endpoints=endpoints_updated, user=INTERNAL_USER, password=password, ignore_revision=True
    )
