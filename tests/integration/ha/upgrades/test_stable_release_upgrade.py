#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from platform import machine

import pytest
from jubilant import Juju

from tests.integration.ha.upgrades.literals import NUM_UNITS, WORKLOAD_VERSION
from tests.integration.helpers import APP_NAME, TLS_NAME, get_leader_unit_name
from tests.integration.helpers_deployment import (
    ExpectedStatus,
    agents_idle,
    apps_active_and_agents_idle,
    does_status_match,
)

logger = logging.getLogger(__name__)

CHARM_CHANNEL = "3.6/stable"
# these revisions are the ones from the first stable release, they are supposed to be kept
CHARM_REVISIONS_TO_DEPLOY = {"x86_64": 119, "aarch64": 120}
REQUIRER_NAME = "requirer-charm"
REQUIRER_TLS_NAME = "requirer-tls-provider"


@pytest.fixture
def requirer_charm(arch: str) -> str:
    """Path to the requirer charm file to use for testing."""
    return f"./tests/integration/client_relations/requirer-charm/requirer-charm_ubuntu@24.04-{arch}.charm"


@pytest.mark.abort_on_fail
def test_deploy_stable_revision(juju_lxd_model: Juju, requirer_charm: str) -> None:
    """Deploy the charm with the first stable release, in a production-like setup."""
    logger.info("Create storage pool for persistent storage")
    juju_lxd_model.cli("create-storage-pool", "etcd-pool", "lxd")

    storage = {
        "data": "etcd-pool,2G",
        "archive": "etcd-pool,2G",
        "logs": "etcd-pool,2G",
    }

    logger.info("Deploy charm from stable, deploy TLS provider and client charm")
    tls_config = {"ca-common-name": "etcd"}
    juju_lxd_model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        storage=storage,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )
    juju_lxd_model.deploy(
        requirer_charm,
        app=REQUIRER_NAME,
        config={"data-interfaces-version": "0"},
    )
    juju_lxd_model.deploy(TLS_NAME, channel="1/stable", config=tls_config)
    juju_lxd_model.deploy(TLS_NAME, channel="1/stable", app=REQUIRER_TLS_NAME, config=tls_config)

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    logger.info("Enable TLS")
    juju_lxd_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    juju_lxd_model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)
    juju_lxd_model.integrate(REQUIRER_NAME, REQUIRER_TLS_NAME)
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, APP_NAME, REQUIRER_NAME, TLS_NAME, REQUIRER_TLS_NAME
        )
    )

    logger.info("Integrate client application")
    juju_lxd_model.integrate(APP_NAME, REQUIRER_NAME)
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, REQUIRER_NAME)
    )


@pytest.mark.abort_on_fail
def test_upgrade_to_latest(charm: str, juju_lxd_model: Juju) -> None:
    """Refresh the charm and upgrade etcd, ensuring high availability while upgrading."""
    # pre-refresh-check
    leader_unit = get_leader_unit_name(juju_lxd_model, APP_NAME)
    logger.info("Running `pre-refresh-check` action")
    pre_refresh_response = juju_lxd_model.run(leader_unit, "pre-refresh-check")
    assert pre_refresh_response.return_code == 0, "action failed"

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        juju_lxd_model.status().get_units(APP_NAME),
        key=lambda unit: int(unit.name.split("/")[1]),
        reverse=True,
    )

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(path=charm, app=APP_NAME)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(lambda status: agents_idle(status, APP_NAME, idle_period=30))
    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info(f"Continue refresh on unit {refresh_order[0]}")
        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        force_refresh_response = juju_lxd_model.run(
            refresh_order[0], "force-refresh-start", {"check-compatibility": False}
        )
        assert force_refresh_response.return_code == 0, "action failed"

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status, expected_status={APP_NAME: ExpectedStatus(app_status=["blocked"])}
        )
    )
    assert "resume-refresh" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message, (
        "Refresh should wait for user to continue with `resume-refresh` action"
    )

    logger.info("Continue refresh on all other units with `resume-refresh` action")
    resume_refresh_response = juju_lxd_model.run(refresh_order[1], "resume-refresh")
    assert resume_refresh_response.return_code == 0, "action failed"

    # wait for upgrade to complete
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )
