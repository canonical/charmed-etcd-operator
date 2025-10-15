#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from platform import machine

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION
from tests.integration.ha.helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    start_continuous_writes,
    stop_continuous_writes,
)
from tests.integration.ha.upgrades.literals import (
    CHARM_CHANNEL,
    CHARM_REVISIONS_TO_DEPLOY,
    NUM_UNITS,
    WORKLOAD_VERSION,
)
from tests.integration.helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_cluster_members,
    get_etcd_version,
    get_secret_by_label,
    get_unit_endpoint,
)
from tests.integration.helpers_deployment import wait_until

logger = logging.getLogger(__name__)


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
    password = secret.get("internal-user-credentials")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await etcd_application.units[0].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME])
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


@pytest.mark.abort_on_fail
async def test_scale_up_during_upgrade(charm: str, ops_test: OpsTest) -> None:
    """Add a unit to an etcd cluster during an upgrade."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME])

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get("internal-user-credentials")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        etcd_application.units,
        key=lambda unit: int(unit.name.split("/")[1]),
        reverse=True,
    )

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await refresh_order[0].run_action("force-refresh-start", **{"check-compatibility": False})

    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["blocked"])

    logger.info("Scale up")
    await ops_test.model.applications[APP_NAME].add_unit()
    await wait_until(
        ops_test,
        apps=[APP_NAME],
        apps_statuses=["blocked"],
        wait_for_exact_units=NUM_UNITS + 1,
        idle_period=60,
    )

    logger.info("Scaling up will continue the refresh on the newly added unit")
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS + 1)

    updated_endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    assert_continuous_writes_increasing(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(updated_endpoints, user=INTERNAL_USER, password=password)
    for unit in etcd_application.units:
        unit_endpoint = get_unit_endpoint(ops_test, unit_name=unit.name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit.name} was not upgraded"

        assert any(unit.name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit.name} is not in {cluster_members}"
        )

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)


@pytest.mark.abort_on_fail
async def test_scale_down_during_upgrade(charm: str, ops_test: OpsTest) -> None:
    """Remove a unit from an etcd cluster during an upgrade."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME])

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get("internal-user-credentials")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        etcd_application.units,
        key=lambda unit: int(unit.name.split("/")[1]),
        reverse=True,
    )

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await refresh_order[0].run_action("force-refresh-start", **{"check-compatibility": False})

    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["blocked"])

    logger.info(f"Remove unit {refresh_order[0].name}")
    removed_member_name = refresh_order[0].name.replace("/", "")
    await ops_test.model.applications[APP_NAME].destroy_unit(refresh_order[0].name)

    await wait_until(
        ops_test,
        apps=[APP_NAME],
        apps_statuses=["blocked"],
        wait_for_exact_units=NUM_UNITS - 1,
        idle_period=60,
    )

    updated_endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    assert_continuous_writes_increasing(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )

    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        # wait for the action to return before continuing, to avoid being too quick
        force_refresh_action = await refresh_order[1].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )
        force_refresh_response = await force_refresh_action.wait()
        assert force_refresh_response.results.get("return-code") == 0, "action failed"

    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["blocked"])

    logger.info("Complete refresh with `resume-refresh` action")
    resume_refresh_action = await refresh_order[-1].run_action("resume-refresh")
    resume_refresh_response = await resume_refresh_action.wait()
    assert resume_refresh_response.results.get("return-code") == 0, "action failed"

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS - 1)

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(updated_endpoints, user=INTERNAL_USER, password=password)
    member_names = [member["name"] for member in cluster_members]

    for unit in etcd_application.units:
        unit_endpoint = get_unit_endpoint(ops_test, unit_name=unit.name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit.name} was not upgraded"

        assert any(unit.name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit.name} is not in {cluster_members}"
        )

    assert removed_member_name not in member_names, (
        f"{removed_member_name} still in cluster members"
    )
    logger.info(f"{removed_member_name} not in cluster members")

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    # upgrading a 2-unit cluster causes some writes to fail during snap refresh -> ignore revisions
    assert_continuous_writes_consistent(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)
