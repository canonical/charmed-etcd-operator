#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from juju.application import Application
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION, TuningOptions
from statuses import ConfigStatuses

from ..helpers import (
    APP_NAME,
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


@pytest.mark.abort_on_fail
async def test_deploy_with_peer_tls(charm: str, ops_test: OpsTest) -> None:
    """Deploy a cluster with three units and peer-certificates."""
    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd"}
    await ops_test.model.deploy(TLS_NAME, channel="1/edge", config=tls_config)

    if await existing_app(ops_test):
        return

    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    await ops_test.model.deploy(charm, num_units=NUM_UNITS)


@pytest.mark.abort_on_fail
async def test_disable_and_enable_peer_tls(ops_test: OpsTest) -> None:
    """Disable and enable peer TLS on a running cluster.

    By enabling/disabling the peer TLS option, we initiate rolling restarts on the etcd cluster.
    This will cause transfer of Raft leadership, and we want to make sure the cluster is available
    for writing data all the time.
    """
    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates relations")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000)

    app_name = (await existing_app(ops_test)) or APP_NAME
    etcd_app: Application = ops_test.model.applications[app_name]

    endpoints = get_cluster_endpoints(ops_test, app_name)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app_name}.app")
    password = secret.get("internal-user-credentials")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # disable peer TLS and check continuous writes
    logger.info("Removing peer-certificates relations")
    await etcd_app.remove_relation("peer-certificates", f"{TLS_NAME}:certificates")
    await wait_until(ops_test, apps=[app_name], timeout=1000)

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # enable peer TLS and check continuous writes
    logger.info("Integrating peer-certificates relations")
    await ops_test.model.integrate(f"{app_name}:peer-certificates", TLS_NAME)
    await wait_until(ops_test, apps=[app_name], timeout=1000)

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
async def test_tuning_config_options(ops_test: OpsTest) -> None:
    """Tune the network latency parameters in etcd and ensure the cluster is available."""
    app_name = (await existing_app(ops_test)) or APP_NAME
    await wait_until(ops_test, apps=[app_name], wait_for_exact_units=NUM_UNITS)

    # start writing data to the cluster
    endpoints = get_cluster_endpoints(ops_test, app_name)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app_name}.app")
    password = secret.get("internal-user-credentials")
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # set tuning parameters to reasonable values in high-latency environments
    await ops_test.model.applications[app_name].set_config(
        {
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: "5000",
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: "500",
        }
    )

    # wait for the rolling restart to apply the config changes
    await wait_until(ops_test, apps=[app_name], wait_for_exact_units=NUM_UNITS)

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
async def test_invalid_tuning_config_options(ops_test: OpsTest) -> None:
    """Ensure the cluster keeps running with invalid tuning options."""
    app_name = (await existing_app(ops_test)) or APP_NAME
    await wait_until(ops_test, apps=[app_name], wait_for_exact_units=NUM_UNITS)

    # start writing data to the cluster
    endpoints = get_cluster_endpoints(ops_test, app_name)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app_name}.app")
    password = secret.get("internal-user-credentials")
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # set tuning parameters to invalid values (election timeout must be >= 10x heartbeat interval)
    await ops_test.model.applications[app_name].set_config(
        {
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: "4000",
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: "500",
        }
    )

    await wait_until(
        ops_test,
        apps=[app_name],
        apps_full_statuses={
            APP_NAME: [ConfigStatuses.TUNING_CONFIG_INVALID.value],
        },
        wait_for_exact_units=NUM_UNITS,
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
