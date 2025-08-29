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
    is_endpoint_up,
)
from ..helpers_deployment import wait_until
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    disable_etcd_service,
    start_continuous_writes,
    stop_continuous_writes,
)
from .helpers_network import (
    cut_network_from_unit_with_ip_change,
    hostname_from_unit,
    ip_address_from_unit,
    restore_network_for_unit_with_ip_change,
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

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await wait_until(ops_test, apps=[APP_NAME])
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
async def test_disaster_recovery_during_upgrade(charm: str, ops_test: OpsTest) -> None:
    """Recover a failed cluster of two units during an upgrade."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=2,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=2)

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=2)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await etcd_application.units[-1].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )

    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=2)

    # the cluster looses consensus if one of two units is unavailable
    logger.info(f"Pause etcd on unit {etcd_application.units[0].name} to force cluster failure")
    await disable_etcd_service(ops_test, unit_name=etcd_application.units[0].name)

    # cluster failure will be detected on `update_status`
    async with ops_test.fast_forward("30s"):
        await wait_until(
            ops_test, apps=[APP_NAME], wait_for_exact_units=2, units_statuses=["active", "blocked"]
        )

    assert "Cluster failure" in etcd_application.units[-1].workload_status_message, (
        "Cluster failure not detected"
    )

    for unit in etcd_application.units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    logger.info("Rebuilding cluster after majority failure")
    rebuild_action = await leader_unit.run_action("rebuild-cluster")
    rebuild_response = await rebuild_action.wait()
    assert rebuild_response.results.get("return-code") == 0, "rebuild failed"

    async with ops_test.fast_forward("30s"):
        await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=2, idle_period=10)

    # cluster should be recovered again
    assert "Cluster failure" not in etcd_application.units[-1].workload_status_message, (
        "Cluster could not be recovered"
    )
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    assert "resume-refresh" in etcd_application.status_message, (
        "Refresh should wait for user to continue with `resume-refresh` action"
    )

    logger.info("Continue refresh on all other units with `resume-refresh` action")
    resume_refresh_action = await etcd_application.units[0].run_action("resume-refresh")
    resume_refresh_response = await resume_refresh_action.wait()
    assert resume_refresh_response.results.get("return-code") == 0, "action failed"

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=2)
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
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
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)


@pytest.mark.abort_on_fail
async def test_ip_address_change_during_upgrade(charm: str, ops_test: OpsTest) -> None:
    """Process an updated ip address during an upgrade."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

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
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)

    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info(f"Continue refresh on unit {refresh_order[0].name}")
        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        force_refresh_action = await refresh_order[0].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )
        force_refresh_response = await force_refresh_action.wait()
        assert force_refresh_response.results.get("return-code") == 0, "action failed"

    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["blocked"])

    logger.info(f"Force new ip address for {refresh_order[-1].name}")
    ip_renewal_hostname = await hostname_from_unit(ops_test, unit_name=refresh_order[-1].name)
    old_unit_ip = await ip_address_from_unit(ops_test, unit_name=refresh_order[-1].name)
    cut_network_from_unit_with_ip_change(ip_renewal_hostname)

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # reconnect the network for the disconnected unit
    restore_network_for_unit_with_ip_change(ip_renewal_hostname)
    logger.info(f"Network has been restored for {refresh_order[-1].name}")

    # ensure the unit is up again
    new_unit_ip = await ip_address_from_unit(ops_test, unit_name=refresh_order[-1].name)
    unit_endpoint = get_unit_endpoint(ops_test, unit_name=refresh_order[-1].name)
    assert is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
    logger.info(f"{refresh_order[-1].name} is available again with new ip {new_unit_ip}")

    logger.info("Continue refresh with `resume-refresh` action")
    resume_refresh_action = await refresh_order[1].run_action("resume-refresh")
    resume_refresh_response = await resume_refresh_action.wait()
    assert resume_refresh_response.results.get("return-code") == 0, "action failed"

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)
    endpoints_updated = endpoints.replace(old_unit_ip, new_unit_ip)
    assert_continuous_writes_increasing(
        endpoints=endpoints_updated, user=INTERNAL_USER, password=password
    )

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints_updated, user=INTERNAL_USER, password=password)
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
        endpoints=endpoints_updated, user=INTERNAL_USER, password=password
    )
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)
