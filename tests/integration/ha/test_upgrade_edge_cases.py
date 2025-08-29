#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from platform import machine

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION

from ..helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_cluster_members,
    get_etcd_version,
    get_secret_by_label,
    get_unit_endpoint,
)
from ..helpers_deployment import wait_until
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    disable_etcd_service,
    enable_etcd_service,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3
CHARM_CHANNEL = "3.6/edge"
CHARM_REVISIONS_TO_DEPLOY = {"x86_64": 89, "aarch64": 88}
WORKLOAD_VERSION = {"previous": "3.6.1", "target": "3.6.2"}


@pytest.mark.abort_on_fail
async def test_upgrade_single_unit_cluster(charm: str, ops_test: OpsTest) -> None:
    """Deploy one unit of etcd and upgrade it - without HA."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=1,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME])

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # will be marked "incompatible" if rollback is not to the same revision as initially deployed
    await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Rollback is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await etcd_application.units[0].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd version")
    unit_endpoint = get_unit_endpoint(
        ops_test, unit_name=etcd_application.units[0].name, app_name=APP_NAME
    )
    assert (
        get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
        == WORKLOAD_VERSION["target"]
    ), "etcd was not upgraded"

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)
