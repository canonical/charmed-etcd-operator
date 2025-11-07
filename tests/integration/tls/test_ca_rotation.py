#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import time

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION, TLSType

from ..helpers import (
    APP_NAME,
    download_client_certificate_from_unit_jubilant,
    get_certificate_from_unit_jubilant,
    get_cluster_endpoints_jubilant,
    get_cluster_members,
    get_key,
    get_leader_unit_name_jubilant,
    get_secret_by_label_jubilant,
    put_key,
)
from ..helpers_deployment import apps_active_and_agents_idle, tls_peer_certs_expiring

logger = logging.getLogger(__name__)

TLS_NAME = "self-signed-certificates"
NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"
CERTIFICATE_EXPIRY_TIME = 90


@pytest.mark.abort_on_fail
def test_build_and_deploy_with_tls(charm: str, juju_lxd_model: Juju) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """
    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd"}
    juju_lxd_model.deploy(TLS_NAME, channel="1/edge", config=tls_config)

    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    juju_lxd_model.deploy(charm, num_units=NUM_UNITS)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    juju_lxd_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    juju_lxd_model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, TLS_NAME, idle_period=60)
    )

    endpoints = get_cluster_endpoints_jubilant(juju_lxd_model, APP_NAME, tls_enabled=True)
    download_client_certificate_from_unit_jubilant(juju_lxd_model, APP_NAME)

    # make sure data can be written to the cluster
    secret = get_secret_by_label_jubilant(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
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
def test_ca_rotation_by_config_change(juju_lxd_model: Juju) -> None:
    """Test the CA rotation.

    The CA certificate should be rotated and the cluster should still be accessible.
    The rotation is triggered by updating the config for `ca-common-name` on the TLS provider side.
    """
    # Rotate the CA certificate
    logger.info("Getting the current CA certificates")
    leader_unit = get_leader_unit_name_jubilant(juju_lxd_model, APP_NAME)
    current_peer_ca = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.PEER, is_ca=True
    )
    assert current_peer_ca, "Failed to get the current peer CA certificate"

    current_client_ca = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.CLIENT, is_ca=True
    )
    assert current_client_ca, "Failed to get the current client CA certificate"

    current_peer_certificate = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.PEER, is_ca=False
    )
    assert current_peer_certificate, "Failed to get the current peer certificate"

    current_client_certificate = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.CLIENT, is_ca=False
    )
    assert current_client_certificate, "Failed to get the current client certificate"

    logger.info("Rotating the CA certificate")
    tls_config = {"ca-common-name": "new-etcd-ca"}
    juju_lxd_model.config(app=TLS_NAME, values=tls_config)

    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    logger.info("Checking if the CA certificates are rotated")
    new_peer_ca = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.PEER, is_ca=True
    )
    assert new_peer_ca, "Failed to get the new peer CA certificate"

    new_client_ca = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.CLIENT, is_ca=True
    )
    assert new_client_ca, "Failed to get the new client CA certificate"

    new_peer_certificate = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.PEER, is_ca=False
    )
    assert new_peer_certificate, "Failed to get the new peer certificate"

    new_client_certificate = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.CLIENT, is_ca=False
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

    download_client_certificate_from_unit_jubilant(juju_lxd_model, APP_NAME)
    # Check if the cluster is still accessible
    logger.info("Checking if the cluster is still accessible")
    endpoints = get_cluster_endpoints_jubilant(juju_lxd_model, APP_NAME, tls_enabled=True)

    secret = get_secret_by_label_jubilant(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
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


def _prepare_units_for_ca_expiration_test(juju: Juju) -> None:
    """Prepare the units for the CA expiration test."""
    for unit_name in juju.status().get_units(APP_NAME).keys():
        logger.info("Updating renewal relative time to 0.6 for unit %s", unit_name)
        search_expression = "\\(refresh_events=\\[self.refresh_tls_certificates_event\\],\\)"
        replace_expression = "\\1renewal_relative_time=0.6,"
        file = f"/var/lib/juju/agents/unit-{unit_name.replace('/', '-')}/charm/src/events/tls.py"
        juju.ssh(
            command=f"sudo sed -i 's|{search_expression}|{replace_expression}|' {file}",
            target=unit_name,
        )


@pytest.mark.abort_on_fail
def test_ca_rotation_by_expiration(juju_lxd_model: Juju) -> None:
    """Test the CA rotation.

    The CA certificate should be rotated and the cluster should still be accessible.
    The rotation is triggered by expiring certificates.
    """
    _prepare_units_for_ca_expiration_test(juju_lxd_model)

    # cert validity should be enough time for the rotation to happen
    # even with health checks failing because of invalid certs
    # CA validity must be 2x cert validity to be considered valid
    ca_root_validity = 20  # in minutes
    certificate_validity = 10  # in minutes
    logger.info(
        "Adjusting validity of the CA to %s min and certificates to %s min",
        ca_root_validity,
        certificate_validity,
    )
    tls_config = {
        "root-ca-validity": f"{ca_root_validity}m",
        "certificate-validity": f"{certificate_validity}m",
    }
    juju_lxd_model.config(app=TLS_NAME, values=tls_config)

    juju_lxd_model.wait(tls_peer_certs_expiring)

    logger.info("Getting the current CA certificates")
    leader_unit = get_leader_unit_name_jubilant(juju_lxd_model, APP_NAME)
    current_peer_ca = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.PEER, is_ca=True
    )
    assert current_peer_ca, "Failed to get the current peer CA certificate"

    current_client_ca = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.CLIENT, is_ca=True
    )
    assert current_client_ca, "Failed to get the current client CA certificate"

    current_peer_certificate = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.PEER, is_ca=False
    )
    assert current_peer_certificate, "Failed to get the current peer certificate"

    current_client_certificate = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.CLIENT, is_ca=False
    )
    assert current_client_certificate, "Failed to get the current client certificate"

    # CA certificate will be renewed at ca validity - certificate validity = 10 minutes
    # Certificates will be renewed at certificate validity * 0.6 = 6 minutes
    # we need to wait for the first certificate renewal after the CA renewal
    # We need to wait certificate_validity * 0.6 * 2 + some buffer time starting from the config change
    logger.info("Waiting for the certificates to expire after CA renewal")
    waiting_time = (certificate_validity * 60 * 0.6 * 2) + 30
    logger.info(f"Certificates will expire in {waiting_time} seconds")
    time.sleep(waiting_time)

    juju_lxd_model.wait(tls_peer_certs_expiring)

    logger.info("Checking if the CA certificates are rotated")
    new_peer_ca = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.PEER, is_ca=True
    )
    assert new_peer_ca, "Failed to get the new peer CA certificate"

    new_client_ca = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.CLIENT, is_ca=True
    )
    assert new_client_ca, "Failed to get the new client CA certificate"

    new_peer_certificate = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.PEER, is_ca=False
    )
    assert new_peer_certificate, "Failed to get the new peer certificate"

    new_client_certificate = get_certificate_from_unit_jubilant(
        juju_lxd_model, leader_unit, cert_type=TLSType.CLIENT, is_ca=False
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

    download_client_certificate_from_unit_jubilant(juju_lxd_model, APP_NAME)
    # Check if the cluster is still accessible
    logger.info("Checking if the cluster is still accessible")
    endpoints = get_cluster_endpoints_jubilant(juju_lxd_model, APP_NAME, tls_enabled=True)

    cluster_members = get_cluster_members(endpoints, tls_enabled=True)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    secret = get_secret_by_label_jubilant(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
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
