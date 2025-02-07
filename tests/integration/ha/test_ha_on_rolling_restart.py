#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from juju.application import Application
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION

from ..helpers import (
    APP_NAME,
    CHARM_PATH,
    get_cluster_endpoints,
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

TLS_NAME = "self-signed-certificates"
NUM_UNITS = 3


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_deploy_with_peer_tls(ops_test: OpsTest) -> None:
    """Deploy a cluster with three units and peer-certificates."""
    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd"}
    await ops_test.model.deploy(TLS_NAME, channel="edge", config=tls_config)

    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    await ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates relations")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_disable_and_enable_peer_tls(ops_test: OpsTest) -> None:
    """Disable and enable peer TLS on a running cluster.

    By enabling/disabling the peer TLS option, we initiate rolling restarts on the etcd cluster.
    This will cause transfer of Raft leadership, and we want to make sure the cluster is available
    for writing data all the time.
    """
    app_name = (await existing_app(ops_test)) or APP_NAME
    etcd_app: Application = ops_test.model.applications[app_name]

    endpoints = get_cluster_endpoints(ops_test, app_name)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app_name}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # disable peer TLS and check continuous writes
    logger.info("Removing peer-certificates relations")
    await etcd_app.remove_relation("peer-certificates", f"{TLS_NAME}:certificates")
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000)

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # enable peer TLS and check continuous writes
    logger.info("Integrating peer-certificates relations")
    await ops_test.model.integrate(f"{app_name}:peer-certificates", TLS_NAME)
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000)

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
