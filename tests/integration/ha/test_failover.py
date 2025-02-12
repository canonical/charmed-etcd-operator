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
    logger.info(f"stopping etcd process on unit {initial_raft_leader}")
    await send_process_control_signal(
        unit_name=leader_unit, model_full_name=ops_test.model_full_name, signal="SIGKILL"
    )

    # now check the availability and formation of the cluster
    # restart time of systemd is 20 sec
    time.sleep(25)
    new_raft_leader = get_raft_leader(endpoints=endpoints)
    logger.info(f"new raft leader: {new_raft_leader}")
    assert new_raft_leader != initial_raft_leader, (
        "raft leadership not transferred after stop of leader"
    )

    # ensure the stopped unit was restarted (via systemctl)
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

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
    )

    # ensure data is written in the cluster
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
    # that's why we will miss writes here, and therefore ignore the revision of the key
    assert_continuous_writes_consistent(
        endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )
