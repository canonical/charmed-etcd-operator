#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from time import sleep

import pytest
from jubilant import Juju, TaskError

from literals import CLIENT_PORT, INTERNAL_USER, PEER_RELATION

from ..helpers import (
    APP_NAME,
    fast_forward,
    get_app_status,
    get_cluster_endpoints,
    get_cluster_members,
    get_leader_unit_name,
    get_secret_by_label,
)
from ..helpers_deployment import (
    are_apps_active_and_agents_idle,
    wait_until_apps_active_and_agents_idle,
)
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 5


@pytest.mark.abort_on_fail
def test_build_and_deploy(charm: str, juju_lxd_model: Juju) -> None:
    """Build and deploy the charm."""
    juju_lxd_model.deploy(charm, num_units=NUM_UNITS)
    juju_lxd_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME), timeout=1400
    )
    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_membership_reconfiguration_after_unit_loss(juju_lxd_model: Juju) -> None:
    """Make sure a forcefully removed unit is removed as cluster member."""
    units = list(juju_lxd_model.status().get_units(APP_NAME))
    unit_to_remove = units[-1]
    removed_member_name = unit_to_remove.replace("/", "")
    logger.info(f"Forcefully removing unit {unit_to_remove}")

    juju_lxd_model.remove_unit(unit_to_remove, force=True)

    # wait for the next `update_status` for the cluster membership to be updated
    with fast_forward(juju_lxd_model, 15):
        juju_lxd_model.wait(
            lambda status: are_apps_active_and_agents_idle(
                status, APP_NAME, unit_count=NUM_UNITS - 1
            )
        )

    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    cluster_members = get_cluster_members(endpoints)
    member_names = [member["name"] for member in cluster_members]
    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        assert unit_name.replace("/", "") in member_names, (
            f"unit {unit_name} not in cluster members"
        )
        logger.info(f"{unit_name} in cluster members")
    assert removed_member_name not in member_names, (
        f"{removed_member_name} still in cluster members"
    )
    logger.info(f"{removed_member_name} not in cluster members")

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_rebuild_on_healthy_cluster(juju_lxd_model: Juju) -> None:
    """Users can run `rebuild-cluster` on a healthy cluster if they use the `force` parameter."""
    juju_lxd_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS - 1)
    )

    leader_unit = get_leader_unit_name(juju_lxd_model, APP_NAME)

    logger.info("Executing rebuild-cluster on healthy cluster - this should fail")
    with pytest.raises(TaskError) as task_error:
        juju_lxd_model.run(leader_unit, "rebuild-cluster")
    assert "Use `force`" in str(task_error), "rebuild should fail without `force` option"

    logger.info("Try again with `force` option")
    rebuild_force_response = juju_lxd_model.run(leader_unit, "rebuild-cluster", {"force": True})
    assert rebuild_force_response.return_code == 0, "rebuild failed"

    # wait for the rebuild to be performed
    juju_lxd_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS - 1)
    )

    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    cluster_members = get_cluster_members(endpoints)
    member_names = [member["name"] for member in cluster_members]
    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        assert unit_name.replace("/", "") in member_names, (
            f"unit {unit_name} not in cluster members"
        )
        logger.info(f"{unit_name} in cluster members")


@pytest.mark.abort_on_fail
def test_recover_from_majority_failure(juju_lxd_model: Juju) -> None:
    """When the majority of the cluster is lost, users can run `rebuild-cluster`."""
    juju_lxd_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS - 1)
    )

    units = list(juju_lxd_model.status().get_units(APP_NAME))
    first_unit_to_remove = units[0]
    first_removed_member_name = first_unit_to_remove.replace("/", "")
    second_unit_to_remove = units[1]
    second_removed_member_name = second_unit_to_remove.replace("/", "")
    logger.info(f"Forcefully removing units {first_unit_to_remove} and {second_unit_to_remove}")

    destroy_unit_cmd = f"remove-unit {first_unit_to_remove} {second_unit_to_remove} --model={juju_lxd_model.model} --force --no-wait --no-prompt"
    juju_lxd_model.cli(*destroy_unit_cmd.split(), include_model=False)

    leader_unit = None

    with fast_forward(juju_lxd_model):
        for x in range(5):
            logger.info(f"Waiting for the units to be removed....{5 - x}")
            sleep(5)

        while leader_unit is None:
            for unit_name, unit_details in get_app_status(juju_lxd_model, APP_NAME).units.items():
                if unit_details.leader:
                    leader_unit = unit_name

    logger.info("Rebuilding cluster after majority failure")
    rebuild_response = juju_lxd_model.run(leader_unit, "rebuild-cluster")
    assert rebuild_response.return_code == 0, "rebuild failed"

    # wait for the rebuild to be performed
    wait_until_apps_active_and_agents_idle(juju_lxd_model, APP_NAME, unit_count=2)

    app_status = get_app_status(juju_lxd_model, APP_NAME)

    endpoints = ",".join(
        [f"'http'://{unit.public_address}:{CLIENT_PORT}" for unit in app_status.units.values()]
    )
    cluster_members = get_cluster_members(endpoints)
    member_names = [member["name"] for member in cluster_members]

    for unit_name in app_status.units:
        assert unit_name.replace("/", "") in member_names, (
            f"unit {unit_name} not in cluster members"
        )
        logger.info(f"{unit_name} in cluster members")
    assert first_removed_member_name not in member_names, (
        f"{first_removed_member_name} still in cluster members"
    )
    logger.info(f"{first_removed_member_name} not in cluster members")
    assert second_removed_member_name not in member_names, (
        f"{second_removed_member_name} still in cluster members"
    )
    logger.info(f"{second_removed_member_name} not in cluster members")
