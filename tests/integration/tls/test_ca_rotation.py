#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import time

import pytest
from data_platform_helpers.advanced_statuses import StatusObjectDict
from juju.application import Application
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION, TLSType
from statuses import TLSStatuses

from ..helpers import (
    APP_NAME,
    download_client_certificate_from_unit,
    get_certificate_from_unit,
    get_cluster_endpoints,
    get_cluster_members,
    get_juju_leader_unit_name,
    get_key,
    get_secret_by_label,
    put_key,
)
from ..helpers_deployment import wait_until

logger = logging.getLogger(__name__)

TLS_NAME = "self-signed-certificates"
NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"
CERTIFICATE_EXPIRY_TIME = 90


async def get_app_status_detail(
    ops_test: OpsTest, app_name: str
) -> tuple[StatusObjectDict, StatusObjectDict]:
    """Get the status detail of the application."""
    for unit in ops_test.model.applications[app_name].units:
        if await unit.is_leader_from_status():
            leader_unit = unit
            break
    else:
        raise ValueError(f"No leader unit found for {app_name}")

    status_detail = await leader_unit.run_action("status-detail")
    response = await status_detail.wait()
    json_output = response.results.get("json-output", {})
    app_statuses = StatusObjectDict.model_validate_json(json_output["app"])
    unit_statuses = StatusObjectDict.model_validate_json(json_output["unit"])
    return app_statuses, unit_statuses


@pytest.mark.abort_on_fail
async def test_build_and_deploy_with_tls(charm: str, ops_test: OpsTest) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """
    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd"}
    await ops_test.model.deploy(TLS_NAME, channel="1/edge", config=tls_config)

    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    await ops_test.model.deploy(charm, num_units=NUM_UNITS)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)
    await wait_until(ops_test, apps=[APP_NAME, TLS_NAME], timeout=1000, idle_period=60)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    await download_client_certificate_from_unit(ops_test, APP_NAME)

    # make sure data can be written to the cluster
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    cluster_members = get_cluster_members(
        endpoints, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")

    assert (
        put_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            value=TEST_VALUE,
            tls_enabled=True,
        )
        == "OK"
    ), "Failed to write key"
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read key"


@pytest.mark.abort_on_fail
async def test_ca_rotation_by_config_change(ops_test: OpsTest) -> None:
    """Test the CA rotation.

    The CA certificate should be rotated and the cluster should still be accessible.
    The rotation is triggered by updating the config for `ca-common-name` on the TLS provider side.
    """
    model = ops_test.model_full_name
    # Rotate the CA certificate
    logger.info("Getting the current CA certificates")
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    current_peer_ca = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.PEER, is_ca=True
    )
    assert current_peer_ca, "Failed to get the current peer CA certificate"

    current_client_ca = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT, is_ca=True
    )
    assert current_client_ca, "Failed to get the current client CA certificate"

    current_peer_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.PEER, is_ca=False
    )
    assert current_peer_certificate, "Failed to get the current peer certificate"

    current_client_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT, is_ca=False
    )
    assert current_client_certificate, "Failed to get the current client certificate"

    logger.info("Rotating the CA certificate")
    tls_config = {"ca-common-name": "new-etcd-ca"}
    tls_app: Application = ops_test.model.applications[TLS_NAME]  # type: ignore
    await tls_app.set_config(tls_config)

    await wait_until(ops_test, apps=[APP_NAME, TLS_NAME])

    logger.info("Checking if the CA certificates are rotated")
    new_peer_ca = get_certificate_from_unit(model, leader_unit, cert_type=TLSType.PEER, is_ca=True)
    assert new_peer_ca, "Failed to get the new peer CA certificate"

    new_client_ca = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT, is_ca=True
    )
    assert new_client_ca, "Failed to get the new client CA certificate"

    new_peer_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.PEER, is_ca=False
    )
    assert new_peer_certificate, "Failed to get the new peer certificate"

    new_client_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT, is_ca=False
    )
    assert new_client_certificate, "Failed to get the new client certificate"

    assert current_peer_ca != new_peer_ca, "Peer CA certificate was not rotated"
    assert current_client_ca != new_client_ca, "Client CA certificate was not rotated"

    logger.info("Both CA certificates are rotated")

    assert current_peer_certificate != new_peer_certificate, "Peer certificate was not rotated"
    assert current_client_certificate != new_client_certificate, (
        "Client certificate was not rotated"
    )

    logger.info("Both certificates are rotated")

    await download_client_certificate_from_unit(ops_test, APP_NAME)
    # Check if the cluster is still accessible
    logger.info("Checking if the cluster is still accessible")
    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"

    password = secret.get(f"{INTERNAL_USER}-password")

    cluster_members = get_cluster_members(
        endpoints, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    logger.info("Reading and writing keys with HTTP peerURLs and HTTPS clientURLs")
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_4",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_4",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.abort_on_fail
async def test_ca_rotation_by_expiration(ops_test: OpsTest) -> None:
    """Test the CA rotation.

    The CA certificate should be rotated and the cluster should still be accessible.
    The rotation is triggered by expiring certificates.
    """
    model = ops_test.model_full_name

    # cert validity should be enough time for the rotation to happen
    # even with health checks failing because of invalid certs
    # CA validity must be 2x cert validity to be considered valid
    logger.info("Adjusting validity of the CA to 12 min and certificates to 6 min")
    tls_config = {"root-ca-validity": "12m", "certificate-validity": "6m"}
    tls_app: Application = ops_test.model.applications[TLS_NAME]  # type: ignore
    await tls_app.set_config(tls_config)
    await wait_until(
        ops_test,
        apps=[APP_NAME, TLS_NAME],
        units_statuses=["maintenance", "active"],
    )

    app_statuses, unit_statuses = await get_app_status_detail(ops_test, APP_NAME)
    tls_statuses = app_statuses.root["tls"].root
    assert tls_statuses == [
        TLSStatuses.TLS_PEER_CERTS_EXPIRING.value,
        TLSStatuses.TLS_CLIENT_CERTS_EXPIRING.value,
    ], "TLS statuses are not as expected"

    logger.info("Getting the current CA certificates")
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    current_peer_ca = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.PEER, is_ca=True
    )
    assert current_peer_ca, "Failed to get the current peer CA certificate"

    current_client_ca = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT, is_ca=True
    )
    assert current_client_ca, "Failed to get the current client CA certificate"

    current_peer_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.PEER, is_ca=False
    )
    assert current_peer_certificate, "Failed to get the current peer certificate"

    current_client_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT, is_ca=False
    )
    assert current_client_certificate, "Failed to get the current client certificate"

    logger.info("Waiting ~5.4m for expiration of certificates - renewed certs will have a new CA")
    # the juju secret expires at 90% of the certificate validity; 360s * 0.9 = 324s
    time.sleep(390)
    await wait_until(
        ops_test,
        apps=[APP_NAME, TLS_NAME],
        units_statuses=["maintenance", "active"],
    )
    app_statuses, unit_statuses = await get_app_status_detail(ops_test, APP_NAME)
    logger.info(f"App statuses after waiting: {app_statuses}")
    logger.info(f"Unit statuses after waiting: {unit_statuses}")
    tls_statuses = app_statuses.root["tls"].root
    assert tls_statuses == [
        TLSStatuses.TLS_PEER_CERTS_EXPIRING.value,
        TLSStatuses.TLS_CLIENT_CERTS_EXPIRING.value,
    ], "TLS statuses are not as expected after waiting for expiration"

    logger.info("Checking if the CA certificates are rotated")
    new_peer_ca = get_certificate_from_unit(model, leader_unit, cert_type=TLSType.PEER, is_ca=True)
    assert new_peer_ca, "Failed to get the new peer CA certificate"

    new_client_ca = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT, is_ca=True
    )
    assert new_client_ca, "Failed to get the new client CA certificate"

    new_peer_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.PEER, is_ca=False
    )
    assert new_peer_certificate, "Failed to get the new peer certificate"

    new_client_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT, is_ca=False
    )
    assert new_client_certificate, "Failed to get the new client certificate"

    assert current_peer_ca != new_peer_ca, "Peer CA certificate was not rotated"
    assert current_client_ca != new_client_ca, "Client CA certificate was not rotated"

    logger.info("Both CA certificates are rotated")

    assert current_peer_certificate != new_peer_certificate, "Peer certificate was not rotated"
    assert current_client_certificate != new_client_certificate, (
        "Client certificate was not rotated"
    )

    logger.info("Both certificates are rotated")

    await download_client_certificate_from_unit(ops_test, APP_NAME)
    # Check if the cluster is still accessible
    logger.info("Checking if the cluster is still accessible")
    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)

    cluster_members = get_cluster_members(endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"

    password = secret.get(f"{INTERNAL_USER}-password")

    logger.info("Reading and writing keys with HTTP peerURLs and HTTPS clientURLs")
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_4",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_4",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"
