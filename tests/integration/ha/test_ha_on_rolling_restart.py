#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION, TuningOptions
from statuses import ConfigStatuses

from ..helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_secret_by_label,
)
from ..helpers_deployment import ExpectedStatus, are_apps_active_and_agents_idle, does_status_match
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    existing_app,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)

TLS_NAME = "self-signed-certificates"
NUM_UNITS = 3


@pytest.mark.abort_on_fail
def test_deploy_with_peer_tls(charm: str, juju_vm_model: Juju) -> None:
    """Deploy a cluster with three units and peer-certificates."""
    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd"}
    juju_vm_model.deploy(TLS_NAME, channel="1/edge", config=tls_config)

    if existing_app(juju_vm_model):
        return

    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    juju_vm_model.deploy(charm, num_units=NUM_UNITS)


@pytest.mark.abort_on_fail
def test_disable_and_enable_peer_tls(juju_vm_model: Juju) -> None:
    """Disable and enable peer TLS on a running cluster.

    By enabling/disabling the peer TLS option, we initiate rolling restarts on the etcd cluster.
    This will cause transfer of Raft leadership, and we want to make sure the cluster is available
    for writing data all the time.
    """
    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates relations")
    juju_vm_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME))

    app_name = existing_app(juju_vm_model) or APP_NAME

    endpoints = get_cluster_endpoints(juju_vm_model, app_name)
    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{app_name}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # disable peer TLS and check continuous writes
    logger.info("Removing peer-certificates relations")
    juju_vm_model.remove_relation(f"{app_name}:peer-certificates", f"{TLS_NAME}:certificates")
    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME))

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # enable peer TLS and check continuous writes
    logger.info("Integrating peer-certificates relations")
    juju_vm_model.integrate(f"{app_name}:peer-certificates", TLS_NAME)
    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME))

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_tuning_config_options(juju_vm_model: Juju) -> None:
    """Tune the network latency parameters in etcd and ensure the cluster is available."""
    app_name = existing_app(juju_vm_model) or APP_NAME
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, app_name, unit_count=NUM_UNITS)
    )

    # start writing data to the cluster
    endpoints = get_cluster_endpoints(juju_vm_model, app_name)
    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{app_name}.app")
    password = secret.get(f"{INTERNAL_USER}-password")
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # set tuning parameters to reasonable values in high-latency environments
    juju_vm_model.config(
        app=app_name,
        values={
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: "5000",
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: "500",
        },
    )

    # wait for the rolling restart to apply the config changes
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)


@pytest.mark.abort_on_fail
def test_invalid_tuning_config_options(juju_vm_model: Juju) -> None:
    """Ensure the cluster keeps running with invalid tuning options."""
    app_name = existing_app(juju_vm_model) or APP_NAME
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, app_name, unit_count=NUM_UNITS)
    )

    # start writing data to the cluster
    endpoints = get_cluster_endpoints(juju_vm_model, app_name)
    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{app_name}.app")
    password = secret.get(f"{INTERNAL_USER}-password")
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # set tuning parameters to invalid values (election timeout must be >= 10x heartbeat interval)
    juju_vm_model.config(
        app=app_name,
        values={
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: "4000",
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: "500",
        },
    )

    juju_vm_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                app_name: ExpectedStatus(
                    app_status=[ConfigStatuses.TUNING_CONFIG_INVALID.value], unit_count=NUM_UNITS
                )
            },
        )
    )

    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
