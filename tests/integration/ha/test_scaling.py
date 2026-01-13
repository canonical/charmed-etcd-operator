#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION

from ..helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_cluster_members,
    get_leader_unit_name,
    get_raft_leader,
    get_secret_by_label,
)
from ..helpers_deployment import apps_active_and_agents_idle
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    existing_app,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
def test_build_and_deploy(charm: str, juju_lxd_model: Juju) -> None:
    """Build and deploy the charm, allowing for skipping if already deployed."""
    # it is possible for users to provide their own cluster for HA testing.
    if existing_app(juju_lxd_model):
        return

    # Deploy the charm and wait for active/idle status
    juju_lxd_model.deploy(charm, num_units=1)
    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME), timeout=1000)

    assert len(juju_lxd_model.status().get_units(APP_NAME)) == 1


@pytest.mark.abort_on_fail
def test_scale_up(juju_lxd_model: Juju) -> None:
    """Make sure new units are added to the etcd cluster without downtime."""
    app = existing_app(juju_lxd_model) or APP_NAME
    init_units_count = len(juju_lxd_model.status().get_units(app))
    init_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    # scale up
    juju_lxd_model.add_unit(app, num_units=2)
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, app, unit_count=init_units_count + 2, idle_period=60
        )
    )
    num_units = len(juju_lxd_model.status().get_units(app))
    assert num_units == init_units_count + 2, (
        f"Expected {init_units_count + 2} units, got {num_units}."
    )

    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(juju_lxd_model, app)

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == init_units_count + 2, (
        f"Expected {init_units_count + 2} cluster members, got {len(cluster_members)}."
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_scale_down(juju_lxd_model: Juju) -> None:
    """Make sure a unit is removed from the etcd cluster without downtime."""
    app = existing_app(juju_lxd_model) or APP_NAME
    init_units_count = len(juju_lxd_model.status().get_units(app))
    init_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    # scale down
    unit_to_remove = list(juju_lxd_model.status().get_units(app))[-1]
    juju_lxd_model.remove_unit(unit_to_remove)
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, app, unit_count=init_units_count - 1, idle_period=60
        )
    )
    num_units = len(juju_lxd_model.status().get_units(app))
    assert num_units == init_units_count - 1, (
        f"Expected {init_units_count - 1} units, got {num_units}."
    )

    # check if unit has been removed from etcd cluster
    endpoints = get_cluster_endpoints(juju_lxd_model, app)

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == init_units_count - 1, (
        f"Expected {init_units_count - 1} cluster members, got {len(cluster_members)}."
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_remove_raft_leader(juju_lxd_model: Juju) -> None:
    """Make sure the etcd cluster is still available when the Raft leader is removed."""
    app = existing_app(juju_lxd_model) or APP_NAME
    init_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    juju_lxd_model.add_unit(app)
    init_units_count = 3
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, app, unit_count=init_units_count, idle_period=60
        )
    )

    # check cluster membership after scaling up
    updated_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    cluster_members = get_cluster_members(updated_endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == init_units_count, (
        f"Expected {init_units_count} cluster members, got {len(cluster_members)}."
    )

    # find and remove the unit that is the current Raft leader
    init_raft_leader = get_raft_leader(
        endpoints=init_endpoints, user=INTERNAL_USER, password=password
    )
    juju_lxd_model.remove_unit(init_raft_leader.replace(app, f"{app}/"))

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, app, unit_count=init_units_count - 1, idle_period=60
        )
    )
    num_units = len(juju_lxd_model.status().get_units(app))
    assert num_units == init_units_count - 1, (
        f"Expected {init_units_count - 1} units, got {num_units}."
    )

    # check if unit has been removed from etcd cluster
    updated_endpoints = get_cluster_endpoints(juju_lxd_model, app)

    cluster_members = get_cluster_members(updated_endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == init_units_count - 1, (
        f"Expected {init_units_count - 1} cluster members, got {len(cluster_members)}."
    )

    # check that another unit is now the Raft leader
    new_raft_leader = get_raft_leader(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )
    assert new_raft_leader != init_raft_leader

    assert_continuous_writes_increasing(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )


@pytest.mark.abort_on_fail
def test_remove_multiple_units(juju_lxd_model: Juju) -> None:
    """Make sure multiple units can be removed from the etcd cluster without downtime."""
    app = existing_app(juju_lxd_model) or APP_NAME
    init_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    juju_lxd_model.add_unit(app)
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, app, unit_count=3, idle_period=60)
    )

    # remove all units except one
    for unit in list(juju_lxd_model.status().get_units(app))[1:]:
        juju_lxd_model.remove_unit(unit)

    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, app, unit_count=1))

    num_units = len(juju_lxd_model.status().get_units(app))
    assert num_units == 1, f"Expected 1 unit, got {num_units}."

    # check if unit has been removed from etcd cluster
    endpoints = get_cluster_endpoints(juju_lxd_model, app)

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == 1, f"Expected 1 cluster member, got {len(cluster_members)}."

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_scale_to_zero_and_back(juju_lxd_model: Juju) -> None:
    """Make sure that removing all units and then adding them again works."""
    app = existing_app(juju_lxd_model) or APP_NAME
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # remove all remaining units
    for unit in juju_lxd_model.status().get_units(app):
        juju_lxd_model.remove_unit(unit)

    juju_lxd_model.wait(
        lambda status: len(juju_lxd_model.status().get_units(app)) == 0, timeout=1000
    )

    # scale up again
    juju_lxd_model.add_unit(app, num_units=3)

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, app, unit_count=3, idle_period=60)
    )

    endpoints = get_cluster_endpoints(juju_lxd_model, app)
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
    # give time to write at least some data
    time.sleep(10)

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == 3, f"Expected 3 cluster members, got {len(cluster_members)}."

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_remove_juju_leader(juju_lxd_model: Juju) -> None:
    """Make sure that removing the juju leader unit works."""
    app = existing_app(juju_lxd_model) or APP_NAME
    init_units_count = len(juju_lxd_model.status().get_units(app))
    init_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    # scale down
    juju_leader_unit = get_leader_unit_name(juju_lxd_model, app)
    juju_lxd_model.remove_unit(juju_leader_unit)

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, app, unit_count=init_units_count - 1)
    )

    num_units = len(juju_lxd_model.status().get_units(app))
    assert num_units == init_units_count - 1, (
        f"Expected {init_units_count - 1} units, got {num_units}."
    )

    # check if unit has been removed from etcd cluster
    endpoints = get_cluster_endpoints(juju_lxd_model, app)

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == init_units_count - 1, (
        f"Expected {init_units_count - 1} cluster members, got {len(cluster_members)}."
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_remove_application(juju_lxd_model: Juju) -> None:
    """Make sure removing the application works."""
    app = existing_app(juju_lxd_model) or APP_NAME

    juju_lxd_model.remove_application(app)
