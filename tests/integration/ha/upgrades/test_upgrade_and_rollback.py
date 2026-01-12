#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from platform import machine

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION
from tests.integration.ha.helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    disable_etcd_service_jubilant,
    enable_etcd_service_jubilant,
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
    get_cluster_endpoints_jubilant,
    get_cluster_members,
    get_etcd_version,
    get_leader_unit_name,
    get_secret_by_label_jubilant,
    get_unit_endpoint_jubilant,
)
from tests.integration.helpers_deployment import (
    ExpectedStatus,
    apps_active_and_agents_idle,
    does_status_match,
)

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
def test_deploy(juju_lxd_model: Juju) -> None:
    """Deploy the charm with the previously released workload version of etcd."""
    juju_lxd_model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS),
        timeout=1000,
    )


@pytest.mark.abort_on_fail
def test_fail_upgrade_and_rollback(charm: str, juju_lxd_model: Juju) -> None:
    """Run a refresh, fail and roll back."""
    endpoints = get_cluster_endpoints_jubilant(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label_jubilant(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        juju_lxd_model.status().get_units(APP_NAME),
        key=lambda unit_name: int(unit_name.split("/")[1]),
        reverse=True,
    )

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(app=APP_NAME, path=charm)

    logger.info(f"Pause etcd service on unit {refresh_order[-1]} to force upgrade to fail")
    disable_etcd_service_jubilant(juju_lxd_model, unit_name=refresh_order[-1])

    # versions will always be marked "incompatible" if refresh to a local version
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info(f"Continue refresh on unit {refresh_order[0]}")
        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        juju_lxd_model.run(
            refresh_order[0],
            "force-refresh-start",
            {"check-compatibility": False, "run-pre-refresh-checks": False},
        )

    # wait for the first refreshed unit to settle
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    assert (
        "health check failed"
        in juju_lxd_model.status()
        .get_units(APP_NAME)
        .get(refresh_order[0])
        .workload_status.message
    ), "Health check after upgrade should have failed"
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info(f"Upgrade failed - roll back to previous version v{WORKLOAD_VERSION['previous']}")
    logger.info(f"Continue etcd service on unit {refresh_order[-1]}")
    enable_etcd_service_jubilant(juju_lxd_model, unit_name=refresh_order[-1])

    # ops_test.application.refresh can't refresh from local to published charm, use command line
    # in `juju refresh`, --switch and --revision are mutually exclusive
    # we can only roll back to the latest released revision from a local charm
    juju_lxd_model.refresh(app=APP_NAME, channel=CHARM_CHANNEL)

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, idle_period=30)
    )
    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        # will be marked "incompatible" if rollback is not to the same revision as initially deployed
        logger.info("Rollback is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        juju_lxd_model.run(refresh_order[0], "force-refresh-start", {"check-compatibility": False})
    elif "Refreshing" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        # rolling back from local to published is only possible to the latest revision
        # if this is not run in a PR, the local built version is the same as the latest published
        # to roll back to the initially deployed version, we need to issue another rollback command
        logger.info("Rolling back to previous revision")
        juju_lxd_model.refresh(app=APP_NAME, revision=CHARM_REVISIONS_TO_DEPLOY[machine()])

    # wait for rollback to complete
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        unit_endpoint = get_unit_endpoint_jubilant(
            juju_lxd_model, unit_name=unit_name, app_name=APP_NAME
        )
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["previous"]
        ), f"unit {unit_name} was not rolled back"

        assert any(unit_name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit_name} is not in {cluster_members}"
        )
    logger.info(f"Successful rollback to v{WORKLOAD_VERSION['previous']} after failed upgrade")

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    # while the upgrade failure was forced, the cluster was shortly not available
    assert_continuous_writes_consistent(
        endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )


@pytest.mark.abort_on_fail
def test_upgrade_to_local(charm: str, juju_lxd_model: Juju) -> None:
    """Refresh the charm and upgrade etcd, ensuring high availability while upgrading."""
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    endpoints = get_cluster_endpoints_jubilant(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label_jubilant(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # pre-refresh-check
    leader_unit = get_leader_unit_name(juju_lxd_model, APP_NAME)
    logger.info("Running `pre-refresh-check` action")
    pre_refresh_response = juju_lxd_model.run(leader_unit, "pre-refresh-check")
    assert pre_refresh_response.return_code == 0, "action failed"

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        juju_lxd_model.status().get_units(APP_NAME),
        key=lambda unit_name: int(unit_name.split("/")[1]),
        reverse=True,
    )

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(app=APP_NAME, path=charm)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, idle_period=30)
    )
    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info(f"Continue refresh on unit {refresh_order[0]}")
        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        force_refresh_response = juju_lxd_model.run(
            refresh_order[0], "force-refresh-start", {"check-compatibility": False}
        )
        assert force_refresh_response.results.get("return-code") == 0, "action failed"

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status, expected_status={APP_NAME: ExpectedStatus(app_status=["blocked"])}
        )
    )
    assert "resume-refresh" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message, (
        "Refresh should wait for user to continue with `resume-refresh` action"
    )

    logger.info("Continue refresh on all other units with `resume-refresh` action")
    resume_refresh_response = juju_lxd_model.run(refresh_order[0], "resume-refresh")
    assert resume_refresh_response.return_code == 0, "action failed"

    # wait for upgrade to complete
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        unit_endpoint = get_unit_endpoint_jubilant(
            juju_lxd_model, unit_name=unit_name, app_name=APP_NAME
        )
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit_name} was not upgraded"

        assert any(unit_name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit_name} is not in {cluster_members}"
        )

    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
