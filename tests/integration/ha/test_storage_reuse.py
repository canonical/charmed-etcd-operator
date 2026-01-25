#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time
from time import sleep

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION

from ..helpers import (
    APP_NAME,
    get_app_status,
    get_cluster_endpoints,
    get_cluster_id,
    get_cluster_members,
    get_secret_by_label,
    get_storage_id,
    get_unit_endpoint,
    is_endpoint_up,
    put_key,
    set_password,
)
from ..helpers_deployment import apps_active_and_agents_idle
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    count_writes,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"


@pytest.mark.abort_on_fail
def test_build_and_deploy(charm: str, juju_lxd_model: Juju) -> None:
    """Deploy the charm with storage volume for data, allowing for skipping if already deployed."""
    # create storage to be used in this test
    # this assumes the test is run on a lxd cloud
    juju_lxd_model.cli("create-storage-pool", "etcd-pool", "lxd")

    storage = {
        "data": "etcd-pool,2G",
        "archive": "etcd-pool,2G",
        "logs": "etcd-pool,2G",
    }

    juju_lxd_model.deploy(charm, num_units=NUM_UNITS, storage=storage)

    # jubilant's wait helper has been avoided intentionally,
    # as it queries the full status of a model (including incompletely set up storage, etc.) thereby erroring out.
    # instead, we fetch the status every 10s and wait for a maximum of 1000s for the app to become active, and agents to settle.
    deadline = time.monotonic() + 1000
    while True:
        current_app_status = get_app_status(juju_lxd_model, APP_NAME)
        is_app_ready = "active" == current_app_status.app_status.current and all(
            "idle" == current_app_status.units.get(unit).juju_status.current
            for unit in current_app_status.units
        )
        if is_app_ready:
            break
        if time.monotonic() >= deadline:
            raise Exception(f"Timed out after waiting 1000s for '{APP_NAME}' to become ready.")
        sleep(10)

    assert len(juju_lxd_model.status().get_units(APP_NAME)) == NUM_UNITS


@pytest.mark.abort_on_fail
def test_attach_storage_after_scale_down(juju_lxd_model: Juju) -> None:
    """Make sure storage can be re-attached after removing a unit."""
    # this test should only be executed with the app we deployed
    app = APP_NAME
    init_units_count = len(juju_lxd_model.status().get_units(app))
    unit = list(juju_lxd_model.status().get_units(app))[-1]
    data_storage_id = get_storage_id(juju_lxd_model, unit, "data")
    archive_storage_id = get_storage_id(juju_lxd_model, unit, "archive")
    log_storage_id = get_storage_id(juju_lxd_model, unit, "log")
    init_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=init_endpoints, user=INTERNAL_USER, password=password)

    # remove the unit
    juju_lxd_model.remove_unit(unit)
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, app, unit_count=init_units_count - 1, idle_period=60
        )
    )

    juju_lxd_model.add_unit(
        app, attach_storage=[data_storage_id, archive_storage_id, log_storage_id]
    )

    new_unit = list(juju_lxd_model.status().get_units(app))[-1]
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, app, unit_count=init_units_count, idle_period=60
        )
    )

    # ensure the newly added endpoint is healthy
    unit_endpoint = get_unit_endpoint(juju_lxd_model, unit_name=new_unit, app_name=app)
    assert is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
    logger.info(f"{new_unit} is available again.")

    # check cluster formation after unit with existing storage was added
    updated_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    cluster_members = get_cluster_members(updated_endpoints, user=INTERNAL_USER, password=password)
    assert new_unit.replace("/", "") in (member["name"] for member in cluster_members), (
        f"{new_unit} is not in {cluster_members}"
    )

    assert len(cluster_members) == init_units_count, (
        f"expected {init_units_count} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    assert_continuous_writes_increasing(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=updated_endpoints, user=INTERNAL_USER, password=password
    )


@pytest.mark.abort_on_fail
def test_attach_storage_after_scale_to_zero(juju_lxd_model: Juju) -> None:
    """Make sure storage can be re-attached after removing all units."""
    # this test should only be executed with the app we deployed
    app = APP_NAME
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")
    initial_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    initial_cluster_id = get_cluster_id(initial_endpoints, user=INTERNAL_USER, password=password)
    initial_writes_value = count_writes(initial_endpoints, INTERNAL_USER, password)

    # remove all units while keeping their storage-ids for later reuse
    storage_ids = []
    for unit in juju_lxd_model.status().get_units(app):
        storage_ids.append(get_storage_id(juju_lxd_model, unit, "data"))
        juju_lxd_model.remove_unit(unit)

    juju_lxd_model.wait(
        lambda status: len(juju_lxd_model.status().get_units(app)) == 0, timeout=1000
    )

    # scale up again re-attaching the storage
    for storage_id in storage_ids:
        juju_lxd_model.add_unit(app, attach_storage=storage_id)

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, app, unit_count=len(storage_ids), idle_period=120
        )
    )

    # check cluster formation after new cluster was forced
    endpoints = get_cluster_endpoints(juju_lxd_model, app)
    new_cluster_id = get_cluster_id(endpoints, user=INTERNAL_USER, password=password)
    assert initial_cluster_id == new_cluster_id, "Cluster ID does not match"

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)

    for unit in juju_lxd_model.status().get_units(app):
        assert any(unit.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit} is not in {cluster_members}"
        )

    assert len(cluster_members) == len(storage_ids), (
        f"expected {len(storage_ids)} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    # ensure data is consistent
    new_writes_value = count_writes(endpoints, INTERNAL_USER, password)
    assert new_writes_value == initial_writes_value, "Data not consistent after reusing storage"

    # start writing data to the cluster and give it some time
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
    time.sleep(30)

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_attach_storage_after_removing_application(charm: str, juju_lxd_model: Juju) -> None:
    """Make sure storage can be re-attached to a completely new etcd application."""
    # this test should only be executed with the app we deployed
    app = APP_NAME
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")
    initial_endpoints = get_cluster_endpoints(juju_lxd_model, app)
    initial_cluster_id = get_cluster_id(initial_endpoints, user=INTERNAL_USER, password=password)
    initial_writes_value = count_writes(initial_endpoints, INTERNAL_USER, password)

    # remove all units except one - we need to know which storage to attach when scaling up again
    for unit in list(juju_lxd_model.status().get_units(app))[1:]:
        juju_lxd_model.remove_unit(unit)

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, app, unit_count=1, idle_period=60)
    )

    # remove the remaining unit after saving the storage id
    unit = list(juju_lxd_model.status().get_units(app))[0]
    storage_id = get_storage_id(juju_lxd_model, unit, "data")

    # remove the entire application
    juju_lxd_model.remove_application(app)
    juju_lxd_model.wait(lambda status: not juju_lxd_model.status().get_units(APP_NAME))

    # deploy new cluster, attaching the storage from the previous last unit to the new first unit
    juju_lxd_model.deploy(charm, attach_storage=storage_id)

    # we are going to deploy a new cluster, but with an existing database
    # that means we need to configure the correct admin password
    set_password(juju_lxd_model, password)

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=1, idle_period=60)
    )

    # make sure the new application/etcd cluster is available
    new_unit = list(juju_lxd_model.status().get_units(app))[-1]
    unit_endpoint = get_unit_endpoint(juju_lxd_model, unit_name=new_unit, app_name=APP_NAME)
    assert (
        put_key(
            unit_endpoint,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    logger.info(f"{new_unit} is available again.")

    # ensure data is consistent
    new_writes_value = count_writes(unit_endpoint, INTERNAL_USER, password)
    assert new_writes_value == initial_writes_value, "Data not consistent after reusing storage"

    # start writing data to the new cluster
    start_continuous_writes(endpoints=unit_endpoint, user=INTERNAL_USER, password=password)

    # scale up
    juju_lxd_model.add_unit(APP_NAME, num_units=2)
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(
            status, APP_NAME, unit_count=NUM_UNITS, idle_period=120
        )
    )

    # check cluster formation
    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    new_cluster_id = get_cluster_id(endpoints, user=INTERNAL_USER, password=password)
    assert initial_cluster_id == new_cluster_id, "Cluster ID does not match"

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)

    for unit in juju_lxd_model.status().get_units(app):
        assert any(unit.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit} is not in {cluster_members}"
        )

    assert len(cluster_members) == NUM_UNITS, (
        f"expected {NUM_UNITS} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
