#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from time import sleep

import pytest
from juju.application import Application
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION
from managers.tls import CertType

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
EXPIRATION_WAITING_TIME = 90


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy_with_tls(ops_test: OpsTest) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """
    assert ops_test.model is not None
    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd", "certificate-validity": "1m"}
    await ops_test.model.deploy(TLS_NAME, channel="edge", config=tls_config)
    # Build and deploy charm from local source folder
    etcd_charm = await ops_test.build_charm(".")
    model = ops_test.model_full_name
    assert model is not None
    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(etcd_charm, num_units=NUM_UNITS)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating the TLS certificates")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    logger.info("Cluster is active and idle")
    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit

    cluster_members = get_cluster_members(model, leader_unit, endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS
    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https")
        assert cluster_member["peerURLs"][0].startswith("https")
    logger.info("Cluster members all have https URLs")

    # make sure data can be written to the cluster
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret
    password = secret.get(f"{INTERNAL_USER}-password")

    logger.info("Writing/reading data to/from the cluster")
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
    )
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
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_turning_off_tls(ops_test: OpsTest) -> None:
    assert ops_test.model
    model = ops_test.model_full_name
    assert model is not None

    # disable TLS and check if the cluster is still accessible
    logger.info("Disabling TLS by removing the TLS certificates")
    etcd_app: Application = ops_test.model.applications[APP_NAME]  # type: ignore
    await etcd_app.remove_relation("peer-certificates", f"{TLS_NAME}:certificates")
    await etcd_app.remove_relation("client-certificates", f"{TLS_NAME}:certificates")

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit
    cluster_members = get_cluster_members(model, leader_unit, endpoints)
    assert len(cluster_members) == NUM_UNITS

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("http://")
        assert cluster_member["peerURLs"][0].startswith("http://")
    logger.info("Cluster members all have http URLs")

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret
    password = secret.get(f"{INTERNAL_USER}-password")
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
    )
    logger.info(f"Cluster is still accessible: key {TEST_KEY} has value {TEST_VALUE}")


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_turning_on_tls(ops_test: OpsTest) -> None:
    assert ops_test.model
    model = ops_test.model_full_name
    assert model is not None

    # enable TLS and check if the cluster is still accessible
    logger.info("Enabling TLS by adding the TLS certificates")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit
    cluster_members = get_cluster_members(model, leader_unit, endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https")
        assert cluster_member["peerURLs"][0].startswith("https")
    logger.info("Cluster members all have https URLs")

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret
    password = secret.get(f"{INTERNAL_USER}-password")
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
    )
    logger.info(f"Cluster is still accessible: key {TEST_KEY} has value {TEST_VALUE}")


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_certificate_expiration(ops_test: OpsTest) -> None:
    assert ops_test.model
    model = ops_test.model_full_name
    assert model is not None

    leader_unit = await get_juju_leader_unit_name(ops_test, APP_NAME)
    assert leader_unit

    # get current certificate
    logger.info("Reading the current certificate from leader unit")
    current_certificate = get_certificate_from_unit(model, leader_unit, cert_type=CertType.CLIENT)
    assert current_certificate

    # wait for certificate to expire
    logger.info(f"Waiting for the certificate to expire {EXPIRATION_WAITING_TIME}s")
    sleep(EXPIRATION_WAITING_TIME)

    # get new certificate
    logger.info("Reading the new certificate from leader unit")
    new_certificate = get_certificate_from_unit(model, leader_unit, cert_type=CertType.CLIENT)
    assert new_certificate

    # check that the certificate has been updated
    assert current_certificate != new_certificate
    logger.info("Certificate has been updated")

    # check that the cluster is still accessible
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret

    password = secret.get(f"{INTERNAL_USER}-password")
    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)

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
    )
    logger.info(f"Cluster is still accessible: key {TEST_KEY} has value {TEST_VALUE}")
