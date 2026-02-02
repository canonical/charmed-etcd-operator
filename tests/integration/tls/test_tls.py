#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess
from time import sleep

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION, TLSType
from statuses import CharmStatuses, TLSStatuses

from ..helpers import (
    APP_NAME,
    TLS_NAME,
    download_client_certificate_from_unit,
    get_certificate_from_unit,
    get_cluster_endpoints,
    get_cluster_members,
    get_key,
    get_leader_unit_name,
    get_secret_by_label,
    put_key,
)
from ..helpers_deployment import (
    ExpectedStatus,
    are_apps_active_and_agents_idle,
    does_status_match,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"
CERTIFICATE_EXPIRY_TIME = 250


@pytest.mark.abort_on_fail
def test_build_and_deploy_with_tls(charm: str, juju_vm_model: Juju) -> None:
    """Build the charm-under-test and deploy it with three units.

    The initial cluster should be formed and accessible.
    """
    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd"}
    juju_vm_model.deploy(TLS_NAME, channel="1/edge", config=tls_config)

    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    juju_vm_model.deploy(charm, num_units=NUM_UNITS)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    juju_vm_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    juju_vm_model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME, idle_period=60)
    )


@pytest.mark.abort_on_fail
def test_tls_enabled(juju_vm_model: Juju) -> None:
    """Check if the TLS has been enabled on app startup."""
    # check if all units have been added to the cluster
    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME, tls_enabled=True)
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)

    # make sure data can be written to the cluster
    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    cluster_members = get_cluster_members(
        endpoints, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

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
def test_disable_tls(juju_vm_model: Juju) -> None:
    """Disable TLS on a running cluster and check if it is still accessible."""
    logger.info("Removing peer-certificates and client-certificates relations")
    juju_vm_model.remove_relation(f"{APP_NAME}:peer-certificates", f"{TLS_NAME}:certificates")
    juju_vm_model.remove_relation(f"{APP_NAME}:client-certificates", f"{TLS_NAME}:certificates")

    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME)
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("http://"), "Client URL is not http"
        assert cluster_member["peerURLs"][0].startswith("http://"), "Peer URL is not http"

    logger.info("All cluster members have HTTP peerURLs and clientURLs")

    logger.info("Reading and writing keys with HTTP peerURLs and clientURLs")
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_2",
        value=TEST_VALUE,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_2",
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.abort_on_fail
def test_enable_tls(juju_vm_model: Juju) -> None:
    """Enable TLS on a running cluster and check if it is still accessible."""
    logger.info("Integrating peer-certificates and client-certificates relations")
    juju_vm_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    juju_vm_model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME, tls_enabled=True)
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)

    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    cluster_members = get_cluster_members(
        endpoints, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
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
        key=f"{TEST_KEY}_3",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_3",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.abort_on_fail
def test_extra_sans_config_option(juju_vm_model: Juju) -> None:
    """Configure extra sans for the TLS certificates."""
    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    logger.info("Set config to invalid sans value")
    config_value = "-my.hostname"
    juju_vm_model.config(app=APP_NAME, values={"certificate-extra-sans": config_value})

    juju_vm_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(
                    app_status=[TLSStatuses.SANS_CONFIG_INVALID.value], unit_count=NUM_UNITS
                )
            },
        )
    )

    download_client_certificate_from_unit(juju_vm_model, APP_NAME)
    client_cert_sans = subprocess.getoutput(
        "openssl x509 -noout -ext subjectAltName -in client.pem "
    )
    assert config_value not in client_cert_sans, (
        f"config value {config_value} found in certificate sans {client_cert_sans}"
    )

    logger.info("Configure valid extra-sans")
    config_value = "server-{unit}.etcd-cluster"
    juju_vm_model.config(app=APP_NAME, values={"certificate-extra-sans": config_value})

    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS),
        timeout=1200,
    )

    # this will download the client cert from application.units[0]
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)
    client_cert_sans = subprocess.getoutput(
        "openssl x509 -noout -ext subjectAltName -in client.pem "
    )
    unit_name = next(iter(juju_vm_model.status().get_units(APP_NAME)))
    expected_sans = config_value.replace("{unit}", unit_name.split("/")[-1])
    assert expected_sans in client_cert_sans, (
        f"expected sans {expected_sans} not found in certificate sans {client_cert_sans}"
    )

    logger.info("Resetting configuration for extra-sans")
    juju_vm_model.config(app=APP_NAME, reset="certificate-extra-sans")

    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    download_client_certificate_from_unit(juju_vm_model, APP_NAME)
    client_cert_sans = subprocess.getoutput(
        "openssl x509 -noout -ext subjectAltName -in client.pem "
    )
    assert expected_sans not in client_cert_sans, (
        f"expected sans {expected_sans} found in certificate sans {client_cert_sans}"
    )


@pytest.mark.abort_on_fail
def test_disable_and_enable_peer_tls(juju_vm_model: Juju) -> None:
    """Disable then enable peer TLS on a running cluster and check if it is still accessible."""
    leader_unit = get_leader_unit_name(juju_vm_model, APP_NAME)

    # get current certificate
    logger.info("Reading the current certificate from leader unit")
    current_certificate = get_certificate_from_unit(
        juju_vm_model, leader_unit, cert_type=TLSType.PEER
    )
    assert current_certificate, "Failed to get current certificate"

    # disable TLS and check if the cluster is still accessible
    logger.info("Removing peer-certificates relation")
    juju_vm_model.remove_relation(f"{APP_NAME}:peer-certificates", f"{TLS_NAME}:certificates")

    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME, tls_enabled=True)
    leader_unit = get_leader_unit_name(juju_vm_model, APP_NAME)
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)

    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    cluster_members = get_cluster_members(
        endpoints, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("http://"), "Peer URL is not http"

    logger.info("All cluster members have HTTPS clientURLs and HTTP peerURLs")

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

    # enable peer TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates relation")
    juju_vm_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)

    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    cluster_members = get_cluster_members(
        endpoints, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    logger.info("Getting new certificate from leader unit")
    new_certificate = get_certificate_from_unit(juju_vm_model, leader_unit, cert_type=TLSType.PEER)
    assert new_certificate, "Failed to get new certificate"
    assert new_certificate != current_certificate, "Certificates are the same after rotation"
    logger.info("Certificates are different after rotation")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read old key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_5",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_5",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.abort_on_fail
def test_disable_and_enable_client_tls(juju_vm_model: Juju) -> None:
    """Disable then enable client TLS on a running cluster and check if it is still accessible."""
    leader_unit = get_leader_unit_name(juju_vm_model, APP_NAME)

    # get current certificate
    logger.info("Reading the current certificate from leader unit")
    current_certificate = get_certificate_from_unit(
        juju_vm_model, leader_unit, cert_type=TLSType.CLIENT
    )
    assert current_certificate, "Failed to get current certificate"

    # disable TLS and check if the cluster is still accessible
    logger.info("Removing client-certificates relation")
    juju_vm_model.remove_relation(f"{APP_NAME}:client-certificates", f"{TLS_NAME}:certificates")

    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME)

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("http://"), "Client URL is not http"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTP clientURLs and HTTPS peerURLs")

    logger.info("Reading and writing keys with HTTPS peerURLs and HTTP clientURLs")
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_4",
        value=TEST_VALUE,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_4",
        )
        == TEST_VALUE
    ), "Failed to read new key"

    # enable client TLS and check if the cluster is still accessible
    logger.info("Integrating client-certificates relation")
    juju_vm_model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME, tls_enabled=True)
    leader_unit = get_leader_unit_name(juju_vm_model, APP_NAME)
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)

    cluster_members = get_cluster_members(
        endpoints, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    logger.info("Getting new certificate from leader unit")
    new_certificate = get_certificate_from_unit(
        juju_vm_model, leader_unit, cert_type=TLSType.CLIENT
    )
    assert new_certificate, "Failed to get new certificate"
    assert new_certificate != current_certificate, "Certificates are the same after rotation"
    logger.info("Certificates are different after rotation")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read old key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_5",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_5",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"


@pytest.mark.abort_on_fail
def test_certificate_expiration(juju_vm_model: Juju) -> None:
    """Test the TLS certificate expiration on a running cluster."""
    # disable TLS and check if the cluster is still accessible
    logger.info("Removing peer-certificates relation and client-certificates relation")
    juju_vm_model.remove_relation(f"{APP_NAME}:peer-certificates", f"{TLS_NAME}:certificates")
    juju_vm_model.remove_relation(f"{APP_NAME}:client-certificates", f"{TLS_NAME}:certificates")

    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"Secret is not set for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME)

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("http://"), "Client URL is not http"
        assert cluster_member["peerURLs"][0].startswith("http://"), "Peer URL is not http"

    logger.info("All cluster members have HTTP peerURLs and clientURLs")

    logger.info("Reading and writing keys with HTTP peerURLs and clientURLs")
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
        )
        == TEST_VALUE
    ), "Failed to read key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_6",
        value=TEST_VALUE,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_6",
        )
        == TEST_VALUE
    ), "Failed to read new key"

    # configure TLS operator with 3m validity as 1m is to little for all following assertions
    logger.info(f"Configuring {TLS_NAME} to issue certificates with 3m validity")
    tls_config = {"ca-common-name": "etcd", "certificate-validity": "3m"}
    juju_vm_model.config(app=TLS_NAME, values=tls_config)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    juju_vm_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    juju_vm_model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    juju_vm_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(unit_status=[TLSStatuses.TLS_PEER_CERTS_EXPIRING.value]),
                TLS_NAME: ExpectedStatus(unit_status=[CharmStatuses.ACTIVE_IDLE.value]),
            },
        )
    )

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME, tls_enabled=True)
    leader_unit = get_leader_unit_name(juju_vm_model, APP_NAME)
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)

    cluster_members = get_cluster_members(
        endpoints, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert len(cluster_members) == NUM_UNITS, f"Cluster members are not equal to {NUM_UNITS}"

    for cluster_member in cluster_members:
        assert cluster_member["clientURLs"][0].startswith("https://"), "Client URL is not https"
        assert cluster_member["peerURLs"][0].startswith("https://"), "Peer URL is not https"

    logger.info("All cluster members have HTTPS peerURLs and clientURLs")

    logger.info("Getting current certificate from leader unit")
    current_peer_certificate = get_certificate_from_unit(
        juju_vm_model, leader_unit, cert_type=TLSType.PEER
    )
    assert current_peer_certificate, "Failed to get current peer certificate"
    current_client_certificate = get_certificate_from_unit(
        juju_vm_model, leader_unit, cert_type=TLSType.CLIENT
    )
    assert current_client_certificate, "Failed to get current client certificate"

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read old key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_7",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
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

    new_peer_certificate = get_certificate_from_unit(
        juju_vm_model, leader_unit, cert_type=TLSType.PEER
    )
    assert new_peer_certificate, "Failed to get new peer certificate"
    assert new_peer_certificate != current_peer_certificate, (
        "Certificates are the same after rotation"
    )

    new_client_certificate = get_certificate_from_unit(
        juju_vm_model, leader_unit, cert_type=TLSType.CLIENT
    )
    assert new_client_certificate, "Failed to get new client certificate"
    assert new_client_certificate != current_client_certificate, (
        "Certificates are the same after rotation"
    )

    logger.info("Certificates are different after rotation")

    logger.info("Reading and writing keys with HTTPS peerURLs and clientURLs")
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)
    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=TEST_KEY,
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read old key"

    assert put_key(
        endpoints,
        user=INTERNAL_USER,
        password=password,
        key=f"{TEST_KEY}_8",
        value=TEST_VALUE,
        tls_enabled=True,
    ), "Failed to write new key"

    assert (
        get_key(
            endpoints,
            user=INTERNAL_USER,
            password=password,
            key=f"{TEST_KEY}_8",
            tls_enabled=True,
        )
        == TEST_VALUE
    ), "Failed to read new key"
