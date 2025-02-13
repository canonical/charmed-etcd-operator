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
    get_secret_by_label,
    get_unit_endpoint,
    put_key,
)
from ..helpers_deployment import wait_until
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    existing_app,
    send_process_control_signal,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3
RESTART_DELAY = 30
TEST_KEY = "test_key"
TEST_VALUE = "42"


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


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_kill_db_process_on_raft_leader(ops_test: OpsTest) -> None:
    """Make sure the cluster can self-heal when the leader goes down."""
    app = (await existing_app(ops_test)) or APP_NAME

    # the deployment is only HA if at least 3 units
    await wait_until(ops_test, apps=[app], wait_for_exact_units=NUM_UNITS)

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

    # axe away the etcd process of the cluster/raft leader
    send_process_control_signal(
        unit_name=leader_unit, model_full_name=ops_test.model_full_name, signal="SIGKILL"
    )

    # now check the availability and formation of the cluster
    time.sleep(RESTART_DELAY)
    new_raft_leader = get_raft_leader(endpoints=endpoints)
    logger.info(f"new raft leader: {new_raft_leader}")
    assert new_raft_leader != initial_raft_leader, (
        "raft leadership not transferred after stop of leader"
    )

    # ensure the stopped unit was restarted
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
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
    logger.info(f"{leader_unit} is available again.")

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
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
async def test_freeze_db_process_on_raft_leader(ops_test: OpsTest) -> None:
    """Make sure the cluster can self-heal when the leader stops."""
    app = (await existing_app(ops_test)) or APP_NAME

    # the deployment is only HA if at least 3 units
    await wait_until(ops_test, apps=[app], wait_for_exact_units=NUM_UNITS)

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

    # freeze the etcd process of the cluster/raft leader
    send_process_control_signal(
        unit_name=leader_unit, model_full_name=ops_test.model_full_name, signal="SIGSTOP"
    )

    # wait until the SIGSTOP fully takes effect
    time.sleep(10)

    # ensure the stopped unit is not reachable
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
    assert not (
        put_key(
            unit_endpoint,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    logger.info(f"{leader_unit} is stopped.")

    # ensure data is still written in the cluster
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # continue the etcd process
    send_process_control_signal(
        unit_name=leader_unit, model_full_name=ops_test.model_full_name, signal="SIGCONT"
    )

    # ensure the stopped unit is reachable again
    time.sleep(10)
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
    logger.info(f"{leader_unit} is available again.")

    # make sure leadership was moved
    new_raft_leader = get_raft_leader(endpoints=endpoints)
    logger.info(f"new raft leader: {new_raft_leader}")
    assert new_raft_leader != initial_raft_leader, (
        "raft leadership not transferred after freeze of leader"
    )

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    stop_continuous_writes()
    # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
    # that's why we will miss writes here, and therefore ignore the revision of the key
    assert_continuous_writes_consistent(
        endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_restart_db_process_on_raft_leader(ops_test: OpsTest) -> None:
    """Make sure the cluster can self-heal when the leader goes down."""
    app = (await existing_app(ops_test)) or APP_NAME

    # the deployment is only HA if at least 3 units
    await wait_until(ops_test, apps=[app], wait_for_exact_units=NUM_UNITS)

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

    # axe away the etcd process of the cluster/raft leader
    send_process_control_signal(
        unit_name=leader_unit, model_full_name=ops_test.model_full_name, signal="SIGTERM"
    )

    # now check the availability and formation of the cluster
    time.sleep(RESTART_DELAY)
    new_raft_leader = get_raft_leader(endpoints=endpoints)
    logger.info(f"new raft leader: {new_raft_leader}")
    assert new_raft_leader != initial_raft_leader, (
        "raft leadership not transferred after stop of leader"
    )

    # ensure the stopped unit was restarted
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
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
    logger.info(f"{leader_unit} is available again.")

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
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
async def test_full_cluster_restart(ops_test: OpsTest) -> None:
    """Make sure the cluster can self-heal after all members went down."""
    app = (await existing_app(ops_test)) or APP_NAME

    # the deployment is only HA if at least 3 units
    await wait_until(ops_test, apps=[app], wait_for_exact_units=NUM_UNITS)

    endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
    time.sleep(10)

    # axe away the etcd process on all units
    for unit in ops_test.model.applications[app].units:
        send_process_control_signal(
            unit_name=unit.name, model_full_name=ops_test.model_full_name, signal="SIGTERM"
        )

    # ensure the cluster is not available
    assert not (
        put_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    logger.info("Cluster is not available after being stopped.")

    # now check the availability and formation of the cluster
    time.sleep(RESTART_DELAY)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster has come back, all {len(cluster_members)} members joined the cluster.")

    # ensure data is written in the cluster
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_full_cluster_crash(ops_test: OpsTest) -> None:
    """Make sure the cluster can self-heal after all members went down."""
    app = (await existing_app(ops_test)) or APP_NAME

    # the deployment is only HA if at least 3 units
    await wait_until(ops_test, apps=[app], wait_for_exact_units=NUM_UNITS)

    endpoints = get_cluster_endpoints(ops_test, app)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
    time.sleep(10)

    # axe away the etcd process on all units
    for unit in ops_test.model.applications[app].units:
        send_process_control_signal(
            unit_name=unit.name, model_full_name=ops_test.model_full_name, signal="SIGKILL"
        )

    # ensure the cluster is not available
    assert not (
        put_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    logger.info("Cluster is not available after crash.")

    # now check the availability and formation of the cluster
    time.sleep(RESTART_DELAY)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster has come back, all {len(cluster_members)} joined the cluster.")

    # ensure data is written in the cluster
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )
