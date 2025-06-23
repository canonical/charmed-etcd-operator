#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION, Status

from ..helpers import (
    APP_NAME,
    CHARM_PATH,
    get_cluster_endpoints,
    get_cluster_members,
    get_secret_by_label,
)
from ..helpers_deployment import wait_until
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 5


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy the charm."""
    await ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS)
    await wait_until(ops_test, apps=[APP_NAME], timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_membership_reconfiguration_after_unit_loss(ops_test: OpsTest) -> None:
    """Make sure a forcefully removed unit is removed as cluster member."""
    unit_to_remove = ops_test.model.applications[APP_NAME].units[-1]
    removed_member_name = unit_to_remove.name.replace("/", "")
    logger.info(f"Forcefully removing unit {unit_to_remove.name}")

    destroy_unit_cmd = f"remove-unit {unit_to_remove.name} --model={ops_test.model.info.name} --force --no-wait --no-prompt"
    return_code, _, _ = await ops_test.juju(*destroy_unit_cmd.split())
    assert return_code == 0, "Failed to remove unit"
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS - 1)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # wait for the next `update_status` for the cluster membership to be updated
    async with ops_test.fast_forward("5s"):
        assert_continuous_writes_increasing(
            endpoints=endpoints, user=INTERNAL_USER, password=password
        )

    cluster_members = get_cluster_members(endpoints)
    member_names = [member["name"] for member in cluster_members]
    for unit in ops_test.model.applications[APP_NAME].units:
        assert unit.name.replace("/", "") in member_names, (
            f"unit {unit.name} not in cluster members"
        )
        logger.info(f"{unit.name} in cluster members")
    assert removed_member_name not in member_names, (
        f"{removed_member_name} still in cluster members"
    )
    logger.info(f"{removed_member_name} not in cluster members")

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_recover_from_majority_failure(ops_test: OpsTest) -> None:
    """When the majority of the cluster is lost, users can run `rebuild-cluster`."""
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS - 1)

    first_unit_to_remove = ops_test.model.applications[APP_NAME].units[0]
    first_removed_member_name = first_unit_to_remove.name.replace("/", "")
    second_unit_to_remove = ops_test.model.applications[APP_NAME].units[1]
    second_removed_member_name = second_unit_to_remove.name.replace("/", "")
    logger.info(
        f"Forcefully removing units {first_unit_to_remove.name} and {second_unit_to_remove.name}"
    )

    destroy_unit_cmd = f"remove-unit {first_unit_to_remove.name} {second_unit_to_remove.name} --model={ops_test.model.info.name} --force --no-wait --no-prompt"
    return_code, _, _ = await ops_test.juju(*destroy_unit_cmd.split())
    assert return_code == 0, "Failed to remove units"

    async with ops_test.fast_forward("10s"):
        await wait_until(
            ops_test,
            apps=[APP_NAME],
            apps_full_statuses={
                APP_NAME: {"blocked": [Status.CLUSTER_FAILED.value.status.message]},
            },
            wait_for_exact_units=2,
        )

    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    logger.info("Rebuilding cluster after majority failure")
    rebuild_action = await leader_unit.run_action("rebuild-cluster")
    rebuild_response = await rebuild_action.wait()
    assert rebuild_response.results.get("return-code") == 0, "rebuild failed"

    # wait for the rebuild to be performed
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=2)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    cluster_members = get_cluster_members(endpoints)
    member_names = [member["name"] for member in cluster_members]
    for unit in ops_test.model.applications[APP_NAME].units:
        assert unit.name.replace("/", "") in member_names, (
            f"unit {unit.name} not in cluster members"
        )
        logger.info(f"{unit.name} in cluster members")
    assert first_removed_member_name not in member_names, (
        f"{first_removed_member_name} still in cluster members"
    )
    logger.info(f"{first_removed_member_name} not in cluster members")
    assert second_removed_member_name not in member_names, (
        f"{second_removed_member_name} still in cluster members"
    )
    logger.info(f"{second_removed_member_name} not in cluster members")
