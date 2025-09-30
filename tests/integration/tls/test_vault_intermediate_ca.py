#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
import logging
import os
import re
import subprocess
from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION

from ..helpers import (
    APP_NAME,
    TLS_NAME,
    add_secret,
    download_client_certificate_from_unit,
    get_cluster_endpoints,
    get_cluster_members,
    get_key,
    get_secret_by_label,
    put_key,
)
from ..helpers_deployment import wait_until

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


@pytest.mark.abort_on_fail
async def test_build_and_deploy_with_tls(charm: str, ops_test: OpsTest) -> None:
    """Set up the TLS provider charms and etcd."""
    _install_dependencies()

    # Deploy the charm and wait for active/idle status
    logger.info("Deploying the charm")
    await ops_test.model.deploy(charm, num_units=NUM_UNITS)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    # Deploy the TLS charms
    tls_config = {"ca-common-name": "etcd"}
    await ops_test.model.deploy(TLS_NAME, channel="1/edge", config=tls_config)
    await ops_test.model.deploy(
        VAULT_NAME,
        channel="1.18/edge",
        config={
            "pki_ca_common_name": DOMAIN_NAME,
            "pki_allow_any_name": True,
            "pki_allow_ip_sans": True,
        },
    )
    await ops_test.model.integrate(f"{VAULT_NAME}:tls-certificates-pki", TLS_NAME)
    await ops_test.model.wait_for_idle(apps=[VAULT_NAME], status="blocked", timeout=1000)


@pytest.mark.abort_on_fail
async def test_initialize_vault(ops_test: OpsTest) -> None:
    """Initialize Vault and wait for it to be ready."""
    vault_app = ops_test.model.applications[VAULT_NAME]
    vault_unit = vault_app.units[0]
    vault_ip = vault_unit.public_address
    secrets = await ops_test.model.list_secrets(show_secrets=True)
    logger.info("Initializing Vault")

    vault_ca = None
    for secret in secrets:
        if secret.label == "self-signed-vault-ca-certificate":
            vault_ca = secret.value.data.get("certificate")

    assert vault_ca, "Vault CA certificate not found in secrets"
    vault_ca = base64.b64decode(vault_ca).decode("utf-8")
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
    secret_id = await add_secret(
        ops_test,
        "vault-token",
        {
            "token": charm_vault_token,
        },
    )

    assert secret_id, "Failed to create vault-token secret"

    await ops_test.model.grant_secret("vault-token", VAULT_NAME)

    action = await vault_unit.run_action(
        "authorize-charm",
        **{
            "secret-id": secret_id,
        },
    )

    action = await action.wait()
    assert action.status == "completed", "Action should succeed"

    await ops_test.model.wait_for_idle(apps=[VAULT_NAME], status="active", timeout=1000)


@pytest.mark.abort_on_fail
async def test_tls_enabled(ops_test: OpsTest) -> None:
    """Check if the TLS has been enabled on app startup."""
    logger.info("Integrating peer-certificates and client-certificates relations")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", VAULT_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", VAULT_NAME)

    await wait_until(ops_test, apps=[APP_NAME, VAULT_NAME])

    # check if all units have been added to the cluster
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
async def test_restrict_certificate_domain(ops_test: OpsTest) -> None:
    """Restrict the allowed domains and request new certificates."""
    logger.info("Restrict allowed certificate domains in Vault")
    vault_domain_config_value = "domain1, domain2, domain3"
    etcd_domain_config_value = "domain3"
    await ops_test.model.applications[VAULT_NAME].set_config(
        {"pki_allowed_domains": vault_domain_config_value}
    )
    await wait_until(ops_test, apps=[VAULT_NAME])

    logger.info("Configure certificate domains in etcd")
    await ops_test.model.applications[APP_NAME].set_config(
        {
            "client-certificate-domain": etcd_domain_config_value,
            "peer-certificate-domain": etcd_domain_config_value,
        }
    )

    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)

    await download_client_certificate_from_unit(ops_test, APP_NAME)
    client_cert_subject = subprocess.getoutput(
        "openssl x509 -noout -subject -in client.pem "
    )
    assert etcd_domain_config_value in client_cert_subject, (
        f"expected domain name {etcd_domain_config_value} not found in certificate subject {client_cert_subject}"
    )
