#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_cluster_members,
    get_juju_leader_unit_name,
    get_key,
    put_key,
)

logger = logging.getLogger(__name__)


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
    await ops_test.model.deploy(etcd_charm, num_units=3)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)

    cluster_members = get_cluster_members(model, leader_unit, endpoints)
    assert len(cluster_members) == 3

    # make sure data can be written to the cluster
    test_key = "test_key"
    test_value = "42"
    assert put_key(model, leader_unit, endpoints, key=test_key, value=test_value) == "OK"
    assert get_key(model, leader_unit, endpoints, key=test_key) == test_value
