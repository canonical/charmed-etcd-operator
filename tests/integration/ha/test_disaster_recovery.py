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
from .helpers_network import (
    cut_network_from_unit_with_ip_change,
    hostname_from_unit,
    is_unit_reachable,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3


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
    assert removed_member_name not in member_names, (
        f"{removed_member_name} still in cluster members"
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_detect_cluster_failure(ops_test: OpsTest) -> None:
    """When the majority of the cluster is lost, the charm should detect cluster failure."""
    logger.info("Cut network from one of the two units to force majority loss")
    unit_to_cut = ops_test.model.applications[APP_NAME].units[-1]
    hostname_to_cut = await hostname_from_unit(ops_test, unit_name=unit_to_cut.name)
    cut_network_from_unit_with_ip_change(hostname_to_cut)

    unit_remaining = ops_test.model.applications[APP_NAME].units[0]
    hostname_remaining = await hostname_from_unit(ops_test, unit_remaining.name)
    assert not is_unit_reachable(hostname_remaining, hostname_to_cut), (
        f"{hostname_to_cut} is reachable from {hostname_remaining}"
    )

    # wait for the next `update_status` to detect the cluster failure
    async with ops_test.fast_forward("10s"):
        await wait_until(
            ops_test,
            apps=[APP_NAME],
            apps_full_statuses={
                APP_NAME: {
                    "blocked": [Status.CLUSTER_FAILED.value.status.message],
                },
            },
        )
