#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from platform import machine

import pytest
from pytest_operator.plugin import OpsTest

from tests.integration.ha.upgrades.literals import NUM_UNITS, WORKLOAD_VERSION
from tests.integration.helpers import APP_NAME, TLS_NAME
from tests.integration.helpers_deployment import wait_until

logger = logging.getLogger(__name__)

CHARM_CHANNEL = "3.6/stable"
# these revisions are the ones from the first stable release, they are supposed to be kept
CHARM_REVISIONS_TO_DEPLOY = {"x86_64": 119, "aarch64": 120}
REQUIRER_NAME = "requirer-charm"
REQUIRER_TLS_NAME = "requirer-tls-provider"


@pytest.fixture
def requirer_charm(platform: str) -> str:
    """Path to the requirer charm file to use for testing."""
    return f"./tests/integration/client_relations/requirer-charm/requirer-charm_ubuntu@24.04-{platform}.charm"


@pytest.mark.abort_on_fail
async def test_deploy_stable_revision(ops_test: OpsTest, requirer_charm: str) -> None:
    """Deploy the charm with the first stable release, in a production-like setup."""
    logger.info("Create storage pool for persistent storage")
    await ops_test.model.create_storage_pool("etcd-pool", "lxd")
    storage = {
        "data": {"pool": "etcd-pool", "size": 2048},
        "archive": {"pool": "etcd-pool", "size": 2048},
        "logs": {"pool": "etcd-pool", "size": 2048},
    }

    logger.info("Deploy charm from stable, deploy TLS provider and client charm")
    tls_config = {"ca-common-name": "etcd"}
    await asyncio.gather(
        ops_test.model.deploy(
            APP_NAME,
            num_units=NUM_UNITS,
            storage=storage,
            channel=CHARM_CHANNEL,
            revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
        ),
        ops_test.model.deploy(
            requirer_charm,
            application_name=REQUIRER_NAME,
            config={"data-interfaces-version": "0"},
        ),
        ops_test.model.deploy(TLS_NAME, channel="1/stable", config=tls_config),
        ops_test.model.deploy(
            TLS_NAME, channel="1/stable", application_name=REQUIRER_TLS_NAME, config=tls_config
        ),
    )
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000, wait_for_exact_units=NUM_UNITS)

    logger.info("Enable TLS")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)
    await ops_test.model.integrate(REQUIRER_NAME, REQUIRER_TLS_NAME)
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME, TLS_NAME, REQUIRER_TLS_NAME])

    logger.info("Integrate client application")
    await ops_test.model.integrate(APP_NAME, REQUIRER_NAME)
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME])


@pytest.mark.abort_on_fail
async def test_upgrade_to_latest(charm: str, ops_test: OpsTest) -> None:
    """Refresh the charm and upgrade etcd, ensuring high availability while upgrading."""
    etcd_application = ops_test.model.applications[APP_NAME]

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
