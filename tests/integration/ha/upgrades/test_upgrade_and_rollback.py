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
    disable_etcd_service,
    enable_etcd_service,
    start_continuous_writes,
    stop_continuous_writes,
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

NUM_UNITS = 3
CHARM_CHANNEL = "3.6/edge"
CHARM_REVISIONS_TO_DEPLOY = {"x86_64": 89, "aarch64": 88}
WORKLOAD_VERSION = {"previous": "3.6.1", "target": "3.6.2"}


@pytest.mark.abort_on_fail
async def test_deploy(ops_test: OpsTest) -> None:
    """Deploy the charm with the previously released workload version of etcd."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME], timeout=1000, wait_for_exact_units=NUM_UNITS)


@pytest.mark.abort_on_fail
async def test_fail_upgrade_and_rollback(charm: str, ops_test: OpsTest) -> None:
    """Run a refresh, fail and roll back."""
    etcd_application = ops_test.model.applications[APP_NAME]

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        etcd_application.units,
        key=lambda unit: int(unit.name.split("/")[1]),
        reverse=True,
    )

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    logger.info(f"Pause etcd service on unit {refresh_order[-1].name} to force upgrade to fail")
    await disable_etcd_service(ops_test, unit_name=refresh_order[-1].name)

    # versions will always be marked "incompatible" if refresh to a local version
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info(f"Continue refresh on unit {refresh_order[0].name}")
        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await refresh_order[0].run_action(
            "force-refresh-start",
            **{"check-compatibility": False, "run-pre-refresh-checks": False},
        )

    # wait for the first refreshed unit to settle
    await ops_test.model.wait_for_idle(apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)
    assert "health check failed" in refresh_order[0].workload_status_message, (
        "Health check after upgrade should have failed"
    )
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info(f"Upgrade failed - roll back to previous version v{WORKLOAD_VERSION['previous']}")
    logger.info(f"Continue etcd service on unit {refresh_order[-1].name}")
    await enable_etcd_service(ops_test, unit_name=refresh_order[-1].name)

    # ops_test.application.refresh can't refresh from local to published charm, use command line
    # in `juju refresh`, --switch and --revision are mutually exclusive
    # we can only roll back to the latest released revision from a local charm
    refresh_cmd = f"refresh {APP_NAME} --model={ops_test.model.info.name} --switch {APP_NAME} --channel {CHARM_CHANNEL}"
    return_code, _, std_err = await ops_test.juju(*refresh_cmd.split())

    # will be marked "incompatible" if rollback is not to the same revision as initially deployed
    await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Rollback is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await refresh_order[0].run_action("force-refresh-start", **{"check-compatibility": False})

    # wait for rollback to complete
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    for unit in etcd_application.units:
        unit_endpoint = get_unit_endpoint(ops_test, unit_name=unit.name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["previous"]
        ), f"unit {unit.name} was not rolled back"

        assert any(unit.name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit.name} is not in {cluster_members}"
        )
    logger.info(f"Successful rollback to v{WORKLOAD_VERSION['previous']} after failed upgrade")

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    # while the upgrade failure was forced, the cluster was shortly not available
    assert_continuous_writes_consistent(
        endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )


@pytest.mark.abort_on_fail
async def test_upgrade_to_local(charm: str, ops_test: OpsTest) -> None:
    """Refresh the charm and upgrade etcd, ensuring high availability while upgrading."""
    etcd_application = ops_test.model.applications[APP_NAME]
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # pre-refresh-check
    for unit in etcd_application.units:
        if await unit.is_leader_from_status():
            leader_unit = unit
    logger.info("Running `pre-refresh-check` action")
    pre_refresh_action = await leader_unit.run_action("pre-refresh-check")
    pre_refresh_response = await pre_refresh_action.wait()
    assert pre_refresh_response.results.get("return-code") == 0, "action failed"

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        etcd_application.units,
        key=lambda unit: int(unit.name.split("/")[1]),
        reverse=True,
    )

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info(f"Continue refresh on unit {refresh_order[0].name}")
        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        force_refresh_action = await refresh_order[0].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )
        force_refresh_response = await force_refresh_action.wait()
        assert force_refresh_response.results.get("return-code") == 0, "action failed"

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["blocked"])
    assert "resume-refresh" in etcd_application.status_message, (
        "Refresh should wait for user to continue with `resume-refresh` action"
    )

    logger.info("Continue refresh on all other units with `resume-refresh` action")
    resume_refresh_action = await refresh_order[1].run_action("resume-refresh")
    resume_refresh_response = await resume_refresh_action.wait()
    assert resume_refresh_response.results.get("return-code") == 0, "action failed"

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)
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

    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
