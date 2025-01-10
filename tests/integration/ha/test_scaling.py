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
    get_cluster_endpoints,
    get_cluster_members,
    get_secret_by_label,
)
from .helpers import (
    count_writes,
    existing_app,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy the charm, allowing for skipping if already deployed."""
    # it is possible for users to provide their own cluster for HA testing.
    if await existing_app(ops_test):
        return

    etcd_charm = await ops_test.build_charm(".")

    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(etcd_charm, num_units=1)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    assert len(ops_test.model.applications[APP_NAME].units) == 1


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
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
    start_continuous_writes(
        ops_test, app_name=app, endpoints=init_endpoints, user=INTERNAL_USER, password=password
    )

    # after some time, get the current count
    time.sleep(10)
    init_writes = count_writes(
        ops_test, app_name=app, endpoints=init_endpoints, user=INTERNAL_USER, password=password
    )

    # scale up
    await ops_test.model.applications[app].add_unit(count=2)
    await ops_test.model.wait_for_idle(
        apps=[app],
        status="active",
        wait_for_exact_units=init_units_count + 2,
        timeout=1000,
    )
    num_units = len(ops_test.model.applications[app].units)
    assert num_units == init_units_count + 2

    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(ops_test, app)

    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == init_units_count + 2

    # check if data was continuously written to the cluster
    stop_continuous_writes()
    final_writes = count_writes(
        ops_test, app_name=app, endpoints=endpoints, user=INTERNAL_USER, password=password
    )
    assert final_writes > init_writes
