#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import re
import subprocess
from pathlib import Path

import jubilant
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION
from statuses import TLSStatuses

from ..helpers import (
    APP_NAME,
    TLS_NAME,
    download_client_certificate_from_unit,
    get_cluster_endpoints,
    get_cluster_members,
    get_key,
    get_secret_by_label,
    put_key,
)
from ..helpers_deployment import ExpectedStatus, are_apps_active_and_agents_idle, does_status_match

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"
CERTIFICATE_EXPIRY_TIME = 250
VAULT_NAME = "vault"
DOMAIN_NAME = "mydomain.com"


def _install_dependencies() -> None:
    """Install dependencies for the test."""
    # Install TLS Certificates interface library
    subprocess.run(
        ["sudo", "snap", "install", "vault"], check=True, text=True, capture_output=True
    )


def test_build_and_deploy_with_tls(charm: str, juju_vm_model: Juju) -> None:
    """Set up the TLS provider charms and etcd."""
    _install_dependencies()

    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    juju_vm_model.deploy(charm, num_units=NUM_UNITS)
    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME))

    # Deploy the TLS charms
    tls_config = {"ca-common-name": "etcd"}
    juju_vm_model.deploy(TLS_NAME, channel="1/edge", config=tls_config)
    juju_vm_model.deploy(
        VAULT_NAME,
        channel="1.18/edge",
        config={
            "pki_ca_common_name": DOMAIN_NAME,
            "pki_allow_any_name": True,
            "pki_allow_ip_sans": True,
        },
    )
    juju_vm_model.integrate(f"{VAULT_NAME}:tls-certificates-pki", TLS_NAME)
    juju_vm_model.wait(lambda status: jubilant.all_blocked(status, VAULT_NAME))


def test_initialize_vault(juju_vm_model: Juju) -> None:
    """Initialize Vault and wait for it to be ready."""
    vault_units = juju_vm_model.status().get_units(VAULT_NAME)
    vault_ip = next(iter(vault_units.values())).public_address
    secrets = juju_vm_model.secrets()
    logger.info("Initializing Vault")

    vault_ca = None
    for secret in secrets:
        if secret.label == "self-signed-vault-ca-certificate":
            vault_ca = juju_vm_model.show_secret(identifier=secret.uri, reveal=True).content.get(
                "certificate"
            )

    assert vault_ca, "Vault CA certificate not found in secrets"

    Path("./vault_ca.pem").write_text(vault_ca)

    vault_env = os.environ.copy()
    vault_env["VAULT_CACERT"] = "./vault_ca.pem"
    vault_env["VAULT_ADDR"] = f"https://{vault_ip}:8200"

    # operator init
    logger.info("Running vault operator init")
    init_cmd = [
        "vault",
        "operator",
        "init",
        "-key-shares=1",
        "-key-threshold=1",
    ]
    init_result = subprocess.run(
        init_cmd, check=True, text=True, capture_output=True, env=vault_env
    )
    logger.info(f"Vault operator init output: {init_result.stdout}")
    init_results_list = [line.strip() for line in init_result.stdout.splitlines() if line.strip()]
    unseal_key = init_results_list[0].split(":")[1].strip()
    root_token = init_results_list[1].split(":")[1].strip()
    vault_env["VAULT_TOKEN"] = root_token

    # operator unseal
    logger.info("Running vault operator unseal")
    unseal_cmd = [
        "vault",
        "operator",
        "unseal",
        unseal_key,
    ]
    unseal_result = subprocess.run(
        unseal_cmd, check=True, text=True, capture_output=True, env=vault_env
    )
    logger.info(f"Vault operator unseal output: {unseal_result.stdout}")

    # authorise vault charm
    # create vault token
    logger.info("Creating Vault token for the vault charm")
    create_token_cmd = [
        "vault",
        "token",
        "create",
        "-ttl=60m",
    ]
    create_token_result = subprocess.run(
        create_token_cmd, check=True, text=True, capture_output=True, env=vault_env
    )
    logger.info(f"Vault token create output: {create_token_result.stdout}")
    token_regex = r"token\s+([\w\.]+)"

    # extract token using regex
    match = re.search(token_regex, create_token_result.stdout)
    assert match, "Failed to extract token from Vault token create output"
    charm_vault_token = match.group(1)
    secret_id = juju_vm_model.add_secret(
        "vault-token",
        {
            "token": charm_vault_token,
        },
    )

    assert secret_id, "Failed to create vault-token secret"

    juju_vm_model.grant_secret("vault-token", VAULT_NAME)

    vault_unit_name = next(iter(vault_units))
    action = juju_vm_model.run(
        unit=vault_unit_name,
        action="authorize-charm",
        params={
            "secret-id": str(secret_id),
        },
    )

    assert action.status == "completed", "Action should succeed"

    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, VAULT_NAME))


def test_tls_enabled(juju_vm_model: Juju) -> None:
    """Check if the TLS has been enabled on app startup."""
    logger.info("Integrating peer-certificates and client-certificates relations")
    juju_vm_model.integrate(f"{APP_NAME}:peer-certificates", VAULT_NAME)
    juju_vm_model.integrate(f"{APP_NAME}:client-certificates", VAULT_NAME)

    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, VAULT_NAME)
    )

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


def test_restrict_certificate_domain(juju_vm_model: Juju) -> None:
    """Restrict the allowed domains and request new certificates."""
    logger.info("Restrict allowed certificate domains in Vault")
    vault_domain_config_value = "domain1, domain2, domain3"
    juju_vm_model.config(app=VAULT_NAME, values={"pki_allowed_domains": vault_domain_config_value})
    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, VAULT_NAME))

    logger.info("Configure certificate domain in etcd")
    etcd_domain_config_value = "domain3"
    juju_vm_model.config(
        app=APP_NAME,
        values={
            "client-certificate-domain": etcd_domain_config_value,
            "peer-certificate-domain": etcd_domain_config_value,
        },
    )

    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    download_client_certificate_from_unit(juju_vm_model, APP_NAME)
    client_cert_subject = subprocess.getoutput("openssl x509 -noout -subject -in client.pem ")
    assert etcd_domain_config_value in client_cert_subject, (
        f"expected domain name {etcd_domain_config_value} not found in certificate subject {client_cert_subject}"
    )
    logger.info("Certificates in etcd updated with new domain")


def test_invalid_certificate_domain(juju_vm_model: Juju) -> None:
    """Ensure no new certificates are requested if invalid domain is configured."""
    logger.info("Set config in etcd to invalid value")
    etcd_invalid_domain_config_value = "192.168.2.200"

    juju_vm_model.config(
        app=APP_NAME, values={"client-certificate-domain": etcd_invalid_domain_config_value}
    )

    juju_vm_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(
                    app_status=[TLSStatuses.CLIENT_DOMAIN_CONFIG_INVALID.value],
                    unit_count=NUM_UNITS,
                )
            },
        )
    )

    download_client_certificate_from_unit(juju_vm_model, APP_NAME)
    client_cert_subject = subprocess.getoutput("openssl x509 -noout -subject -in client.pem ")
    assert etcd_invalid_domain_config_value not in client_cert_subject, (
        f"domain name {etcd_invalid_domain_config_value} found in certificate subject {client_cert_subject}"
    )
    logger.info("Certificates not updated after configuring invalid domain name")
