#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from time import sleep

import pytest
from juju.application import Application
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION, TLSType

from .helpers import (
    APP_NAME,
    get_certificate_from_unit,
    get_cluster_endpoints,
    get_cluster_members,
    get_juju_leader_unit_name,
    get_key,
    get_secret_by_label,
    put_key,
)

logger = logging.getLogger(__name__)

TLS_NAME = "self-signed-certificates"
NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"
CERTIFICATE_EXPIRY_TIME = 90


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy_with_tls(ops_test: OpsTest) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """
    assert ops_test.model is not None, "Model is not set"
    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd"}
    await ops_test.model.deploy(TLS_NAME, channel="edge", config=tls_config)
    # Build and deploy charm from local source folder
    etcd_charm = await ops_test.build_charm(".")
    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    await ops_test.model.deploy(etcd_charm, num_units=NUM_UNITS)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_tls_enabled(ops_test: OpsTest) -> None:
    """Check if the TLS has been enabled on app startup."""
    # check if all units have been added to the cluster
    assert ops_test.model is not None, "Model is not set"
    model = ops_test.model_full_name
    assert model is not None, "Model is not set"
    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"

    cluster_members = get_cluster_members(model, leader_unit, endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    # make sure data can be written to the cluster
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")

    assert (
        put_key(
            model,
            leader_unit,
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
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read key"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_disable_tls(ops_test: OpsTest) -> None:
    """Disable TLS on a running cluster and check if it is still accessible."""
    assert ops_test.model, "Model is not set"
    model = ops_test.model_full_name
    assert model is not None, "Model is not set"

    # disable TLS and check if the cluster is still accessible
    etcd_app: Application = ops_test.model.applications[APP_NAME]  # type: ignore

    logger.info("Removing peer-certificates and client-certificates relations")
    await etcd_app.remove_relation("peer-certificates", f"{TLS_NAME}:certificates")
    await etcd_app.remove_relation("client-certificates", f"{TLS_NAME}:certificates")

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"

    cluster_members = get_cluster_members(model, leader_unit, endpoints)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("http://"), "Client URL is not http"
        assert cluster_member["peerURLs"][0].startswith("http://"), "Peer URL is not http"

    logger.info("All cluster members have HTTP peerURLs and clientURLs")

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"

    password = secret.get(f"{INTERNAL_USER}-password")

    logger.info("Reading and writing keys with HTTP peerURLs and clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_2",
        value=TEST_VALUE,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_2",
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_enable_tls(ops_test: OpsTest) -> None:
    """Enable TLS on a running cluster and check if it is still accessible."""
    assert ops_test.model, "Model is not set"
    model = ops_test.model_full_name
    assert model is not None, "Model is not set"

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"

    cluster_members = get_cluster_members(model, leader_unit, endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"

    password = secret.get(f"{INTERNAL_USER}-password")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_3",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_3",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_disable_and_enable_peer_tls(ops_test: OpsTest) -> None:
    """Disable then enable peer TLS on a running cluster and check if it is still accessible."""
    assert ops_test.model, "Model is not set"
    model = ops_test.model_full_name
    assert model is not None, "Model is not set"

    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"
    # get current certificate
    logger.info("Reading the current certificate from leader unit")
    current_certificate = get_certificate_from_unit(model, leader_unit, cert_type=TLSType.PEER)
    assert current_certificate, "Failed to get current certificate"

    # disable TLS and check if the cluster is still accessible
    etcd_app: Application = ops_test.model.applications[APP_NAME]  # type: ignore
    logger.info("Removing peer-certificates relation")
    await etcd_app.remove_relation("peer-certificates", f"{TLS_NAME}:certificates")

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"
    cluster_members = get_cluster_members(model, leader_unit, endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("http://"), "Peer URL is not http"

    logger.info("All cluster members have HTTPS clientURLs and HTTP peerURLs")

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"

    password = secret.get(f"{INTERNAL_USER}-password")

    logger.info("Reading and writing keys with HTTP peerURLs and HTTPS clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_4",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_4",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"

    # enable peer TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates relation")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    cluster_members = get_cluster_members(model, leader_unit, endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    logger.info("Getting new certificate from leader unit")
    new_certificate = get_certificate_from_unit(model, leader_unit, cert_type=TLSType.PEER)
    assert new_certificate, "Failed to get new certificate"
    assert new_certificate != current_certificate, "Certificates are the same after rotation"
    logger.info("Certificates are different after rotation")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read old key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_5",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_5",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_disable_and_enable_client_tls(ops_test: OpsTest) -> None:
    """Disable then enable client TLS on a running cluster and check if it is still accessible."""
    assert ops_test.model, "Model is not set"
    model = ops_test.model_full_name
    assert model is not None, "Model is not set"

    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"
    # get current certificate
    logger.info("Reading the current certificate from leader unit")
    current_certificate = get_certificate_from_unit(model, leader_unit, cert_type=TLSType.CLIENT)
    assert current_certificate, "Failed to get current certificate"

    # disable TLS and check if the cluster is still accessible
    etcd_app: Application = ops_test.model.applications[APP_NAME]  # type: ignore
    logger.info("Removing client-certificates relation")
    await etcd_app.remove_relation("client-certificates", f"{TLS_NAME}:certificates")

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"
    cluster_members = get_cluster_members(model, leader_unit, endpoints)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("http://"), "Client URL is not http"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTP clientURLs and HTTPS peerURLs")

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"

    password = secret.get(f"{INTERNAL_USER}-password")

    logger.info("Reading and writing keys with HTTPS peerURLs and HTTP clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_4",
        value=TEST_VALUE,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_4",
        )
        == TEST_VALUE
    ), "Failed to read new key"

    # enable client TLS and check if the cluster is still accessible
    logger.info("Integrating client-certificates relation")
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    cluster_members = get_cluster_members(model, leader_unit, endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    logger.info("Getting new certificate from leader unit")
    new_certificate = get_certificate_from_unit(model, leader_unit, cert_type=TLSType.CLIENT)
    assert new_certificate, "Failed to get new certificate"
    assert new_certificate != current_certificate, "Certificates are the same after rotation"
    logger.info("Certificates are different after rotation")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read old key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_5",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_5",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_certificate_expiration(ops_test: OpsTest) -> None:
    """Test the TLS certificate expiration on a running cluster."""
    assert ops_test.model, "Model is not set"
    model = ops_test.model_full_name
    assert model is not None, "Model is not set"

    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"

    # disable TLS and check if the cluster is still accessible
    etcd_app: Application = ops_test.model.applications[APP_NAME]  # type: ignore
    logger.info("Removing peer-certificates relation and client-certificates relation")
    await etcd_app.remove_relation("peer-certificates", f"{TLS_NAME}:certificates")
    await etcd_app.remove_relation("client-certificates", f"{TLS_NAME}:certificates")

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit, "Leader unit is not set"

    cluster_members = get_cluster_members(model, leader_unit, endpoints)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("http://"), "Client URL is not http"
        assert cluster_member["peerURLs"][0].startswith("http://"), "Peer URL is not http"

    logger.info("All cluster members have HTTP peerURLs and clientURLs")

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"

    password = secret.get(f"{INTERNAL_USER}-password")

    logger.info("Reading and writing keys with HTTP peerURLs and clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_6",
        value=TEST_VALUE,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_6",
        )
        == TEST_VALUE
    ), "Failed to read new key"

    # configure TLS operator with 1m validity
    logger.info(f"Configuring {TLS_NAME} to issue certificates with 1m validity")
    tls_config = {"ca-common-name": "etcd", "certificate-validity": "1m"}
    tls_app: Application = ops_test.model.applications[TLS_NAME]  # type: ignore
    await tls_app.set_config(tls_config)

    # enable peer TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    await ops_test.model.wait_for_idle(apps=[APP_NAME, TLS_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    cluster_members = get_cluster_members(model, leader_unit, endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    logger.info("Getting current certificate from leader unit")
    current_peer_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.PEER
    )
    assert current_peer_certificate, "Failed to get current peer certificate"
    current_client_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT
    )
    assert current_client_certificate, "Failed to get current client certificate"

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read old key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_7",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_7",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"

    # wait for certificate to expire
    logger.info("Waiting for certificate to expire")
    sleep(CERTIFICATE_EXPIRY_TIME)

    logger.info("Get new certificates from leader unit")

    new_peer_certificate = get_certificate_from_unit(model, leader_unit, cert_type=TLSType.PEER)
    assert new_peer_certificate, "Failed to get new peer certificate"
    assert new_peer_certificate != current_peer_certificate, (
        "Certificates are the same after rotation"
    )

    new_client_certificate = get_certificate_from_unit(
        model, leader_unit, cert_type=TLSType.CLIENT
    )
    assert new_client_certificate, "Failed to get new client certificate"
    assert new_client_certificate != current_client_certificate, (
        "Certificates are the same after rotation"
    )

    logger.info("Certificates are different after rotation")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read old key"

    assert put_key(
        model,
        leader_unit,
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_8",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            model,
            leader_unit,
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_8",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"
