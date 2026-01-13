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
from tests.integration.helpers_deployment import (
    ExpectedStatus,
    agents_idle,
    apps_active_and_agents_idle,
    does_status_match,
)

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
def test_upgrade_single_unit_cluster(charm: str, juju_lxd_model: Juju) -> None:
    """Deploy one unit of etcd and upgrade it - without HA."""
    juju_lxd_model.deploy(
        APP_NAME,
        num_units=1,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME))

    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(app=APP_NAME, path=charm)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(lambda status: agents_idle(status, APP_NAME, idle_period=30))

    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        juju_lxd_model.run(
            list(juju_lxd_model.status().get_units(APP_NAME))[0],
            "force-refresh-start",
            {"check-compatibility": False},
        )

    # wait for upgrade to complete
    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME))
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd version")
    unit_endpoint = get_unit_endpoint(
        juju_lxd_model,
        unit_name=list(juju_lxd_model.status().get_units(APP_NAME))[0],
        app_name=APP_NAME,
    )
    assert (
        get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
        == WORKLOAD_VERSION["target"]
    ), "etcd was not upgraded"

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    juju_lxd_model.remove_application(APP_NAME, force=True)
    juju_lxd_model.wait(lambda status: not juju_lxd_model.status().get_units(APP_NAME))


@pytest.mark.abort_on_fail
def test_scale_up_during_upgrade(charm: str, juju_lxd_model: Juju) -> None:
    """Add a unit to an etcd cluster during an upgrade."""
    juju_lxd_model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME))

    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(app=APP_NAME, path=charm)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        juju_lxd_model.status().get_units(APP_NAME),
        key=lambda unit_name: int(unit_name.split("/")[1]),
        reverse=True,
    )

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(lambda status: agents_idle(status, APP_NAME, idle_period=30))

    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        juju_lxd_model.run(refresh_order[0], "force-refresh-start", {"check-compatibility": False})

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status, expected_status={APP_NAME: ExpectedStatus(app_status=["blocked"])}
        )
    )

    logger.info("Scale up")
    juju_lxd_model.add_unit(APP_NAME)
    juju_lxd_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(
                    app_status=["blocked"], unit_count=NUM_UNITS + 1, idle_period=60
                )
            },
        )
    )

    logger.info("Scaling up will continue the refresh on the newly added unit")
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS + 1)
    )

    updated_endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    assert_continuous_writes_increasing(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(updated_endpoints, user=INTERNAL_USER, password=password)
    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        unit_endpoint = get_unit_endpoint(juju_lxd_model, unit_name=unit_name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit_name} was not upgraded"

        assert any(unit_name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit_name} is not in {cluster_members}"
        )

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )
    juju_lxd_model.remove_application(APP_NAME, force=True)
    juju_lxd_model.wait(lambda status: not juju_lxd_model.status().get_units(APP_NAME))


@pytest.mark.abort_on_fail
def test_scale_down_during_upgrade(charm: str, juju_lxd_model: Juju) -> None:
    """Remove a unit from an etcd cluster during an upgrade."""
    juju_lxd_model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME))

    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(app=APP_NAME, path=charm)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        juju_lxd_model.status().get_units(APP_NAME),
        key=lambda unit_name: int(unit_name.split("/")[1]),
        reverse=True,
    )

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(lambda status: agents_idle(status, APP_NAME, idle_period=30))

    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        juju_lxd_model.run(refresh_order[0], "force-refresh-start", {"check-compatibility": False})

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status, expected_status={APP_NAME: ExpectedStatus(app_status=["blocked"])}
        )
    )

    logger.info(f"Remove unit {refresh_order[0]}")
    removed_member_name = refresh_order[0].replace("/", "")
    juju_lxd_model.remove_unit(refresh_order[0])

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(
                    app_status=["blocked"], unit_count=NUM_UNITS - 1, idle_period=60
                )
            },
        )
    )

    updated_endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    assert_continuous_writes_increasing(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )

    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        # wait for the action to return before continuing, to avoid being too quick
        force_refresh_response = juju_lxd_model.run(
            refresh_order[1], "force-refresh-start", {"check-compatibility": False}
        )
        assert force_refresh_response.results.get("return-code") == 0, "action failed"

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status, expected_status={APP_NAME: ExpectedStatus(app_status=["blocked"])}
        )
    )

    logger.info("Complete refresh with `resume-refresh` action")
    resume_refresh_response = juju_lxd_model.run(refresh_order[-1], "resume-refresh")
    assert resume_refresh_response.results.get("return-code") == 0, "action failed"

    # wait for upgrade to complete
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS - 1)
    )

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(updated_endpoints, user=INTERNAL_USER, password=password)
    member_names = [member["name"] for member in cluster_members]

    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        unit_endpoint = get_unit_endpoint(juju_lxd_model, unit_name=unit_name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit_name} was not upgraded"

        assert any(unit_name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit_name} is not in {cluster_members}"
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
