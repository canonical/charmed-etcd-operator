#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
import logging

import pytest
from charms.tls_certificates_interface.v4.tls_certificates import LIBID, generate_private_key
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION, TLS_PRIVATE_KEY_CONFIG, TLSType

from ..helpers import (
    APP_NAME,
    add_secret,
    download_client_certificate_from_unit,
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
    await download_client_certificate_from_unit(ops_test, APP_NAME)

    cluster_members = get_cluster_members(endpoints, tls_enabled=True)
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


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_set_private_key(ops_test: OpsTest) -> None:
    """Set a new private key and check if the cluster is still accessible."""
    model = ops_test.model_full_name
    logger.info("Getting current private keys")
    current_private_keys: list[str] = [
        (await get_secret_by_label(ops_test, label=f"{LIBID}-private-key-{i}"))["private-key"]  # type: ignore
        for i in range(NUM_UNITS)
    ]
    assert current_private_keys, "Failed to get current private key"

    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)

    leader_current_peer_cert = get_certificate_from_unit(
        model,  # type: ignore
        leader_unit,
        TLSType.PEER,
    )

    assert leader_current_peer_cert, "Failed to get leader peer certificate"

    leader_current_client_cert = get_certificate_from_unit(
        model,  # type: ignore
        leader_unit,
        TLSType.CLIENT,
    )

    assert leader_current_client_cert, "Failed to get leader client certificate"

    logger.info("Generating new private key")
    new_private_key = generate_private_key().raw

    logger.info("Adding new private key to the model")
    secret_id = await add_secret(
        ops_test,
        TLS_PRIVATE_KEY_CONFIG,
        {"private-key": base64.b64encode(new_private_key.encode()).decode()},
    )
    await ops_test.model.grant_secret(TLS_PRIVATE_KEY_CONFIG, APP_NAME)  # type: ignore

    logger.info("Configuring the application with the new private key")
    await ops_test.model.applications[APP_NAME].set_config({TLS_PRIVATE_KEY_CONFIG: secret_id})  # type: ignore
    await ops_test.model.wait_for_idle(apps=[APP_NAME, TLS_NAME], status="active", timeout=1000)  # type: ignore

    logger.info("Checking if the cluster is still accessible")
    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    cluster_members = get_cluster_members(endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"

    password = secret.get(f"{INTERNAL_USER}-password")

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    )

    assert (
        put_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_new",
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
            key=f"{TEST_KEY}_new",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read key"

    logger.info("Getting new private keys")
    new_private_keys: list[str] = [
        (await get_secret_by_label(ops_test, label=f"{LIBID}-private-key-{i}"))["private-key"]  # type: ignore
        for i in range(NUM_UNITS)
    ]

    assert new_private_keys, "Failed to get new private key"
    for i in range(NUM_UNITS):
        assert new_private_keys[i] != current_private_keys[i], "Private key was not updated"
        assert new_private_keys[i] == new_private_key, "Private key was not updated"

    logger.info("Checking if the certificates have been updated")
    leader_new_peer_cert = get_certificate_from_unit(
        model,  # type: ignore
        leader_unit,
        TLSType.PEER,
    )

    assert leader_new_peer_cert, "Failed to get leader peer certificate"

    leader_new_client_cert = get_certificate_from_unit(
        model,  # type: ignore
        leader_unit,
        TLSType.CLIENT,
    )

    assert leader_new_client_cert, "Failed to get leader client certificate"

    assert leader_current_peer_cert != leader_new_peer_cert, "Peer certificate was not updated"
    assert leader_current_client_cert != leader_new_client_cert, (
        "Client certificate was not updated"
    )
