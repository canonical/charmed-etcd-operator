#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from datetime import timedelta

import pytest
from charmlibs.interfaces.tls_certificates import (
    Certificate,
    CertificateRequestAttributes,
    PrivateKey,
    generate_csr,
)
from jubilant import Juju, TaskError

from literals import EXTERNAL_CLIENTS_RELATION, INTERNAL_USER, PEER_RELATION, TLSType
from statuses import CharmStatuses, ExternalClientsStatuses

from ..helpers import (
    APP_NAME,
    TLS_NAME,
    download_client_certificate_from_unit,
    get_certificate_from_unit,
    get_cluster_endpoints,
    get_role,
    get_secret_by_label,
    get_user,
)
from ..helpers_deployment import ExpectedStatus, are_apps_active_and_agents_idle, does_status_match

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "/test_key"
TEST_VALUE = "42"
REQUIRER_NAME = "requirer-charm"
REQUIRER_TLS_NAME = "requirer-tls-provider"
USER_WITH_FULL_KEYSPACE_ACCESS = "client1.requirer-charm"


@pytest.fixture
def requirer_charm(arch: str) -> str:
    """Path to the requirer charm file to use for testing."""
    return f"./tests/integration/client_relations/requirer-charm/requirer-charm_ubuntu@24.04-{arch}.charm"


def generate_mtls_chain(common_name: str) -> tuple[str, str]:
    """Generate a mtls certificate chain with a CA and an end-entity certificate.

    Args:
        common_name (str): The common name for the end-entity certificate.

    Returns:
        tuple[str, str]: The end-entity certificate and the CA certificate.
    """
    ca_private_key = PrivateKey.generate()
    ca_cert = Certificate.generate_self_signed_ca(
        private_key=ca_private_key,
        validity=timedelta(days=365),
        attributes=CertificateRequestAttributes(common_name="ca_common_name"),
    )

    client_private_key = PrivateKey.generate()
    client_csr = generate_csr(private_key=client_private_key, common_name=common_name)
    client_cert = Certificate.generate(
        csr=client_csr, ca=ca_cert, ca_private_key=ca_private_key, validity=timedelta(days=365)
    )
    return client_cert.raw, ca_cert.raw


def get_requirer_common_names(juju: Juju) -> list[str]:
    """Get the common name of the requirer charm."""
    requirer_unit = next(iter(juju.status().get_units(REQUIRER_NAME)))

    action_result = juju.run(requirer_unit, "get-credentials")
    if action_result.status == "completed":
        return action_result.results["username"].split(",")

    raise ValueError("Failed to get common name from requirer charm")


def get_requirer_mtls_certificates(juju: Juju) -> list[str] | None:
    """Get the mtls certificate from the requirer TLS provider."""
    requirer_unit = next(iter(juju.status().get_units(REQUIRER_NAME)))

    action_result = juju.run(requirer_unit, "get-certificates")
    if action_result.status == "completed":
        return json.loads(action_result.results["certificates"])

    return None


@pytest.mark.parametrize(
    "data_interfaces_version",
    [pytest.param("0", marks=pytest.mark.v0), pytest.param("1", marks=pytest.mark.v1)],
)
def test_build_and_deploy(
    charm: str, requirer_charm: str, juju_vm_model: Juju, data_interfaces_version: str
) -> None:
    """Build and deploy the charm-under-test and the requirer charm."""
    tls_config = {"ca-common-name": "etcd"}
    juju_vm_model.deploy(
        requirer_charm,
        app=REQUIRER_NAME,
        config={"data-interfaces-version": data_interfaces_version},
    )
    juju_vm_model.deploy(charm, num_units=NUM_UNITS)
    juju_vm_model.deploy(TLS_NAME, channel="1/edge", config=tls_config)
    juju_vm_model.deploy(TLS_NAME, channel="1/edge", app=REQUIRER_TLS_NAME, config=tls_config)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    juju_vm_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    juju_vm_model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)
    juju_vm_model.integrate(REQUIRER_NAME, TLS_NAME)
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(
            status, APP_NAME, REQUIRER_NAME, TLS_NAME, REQUIRER_TLS_NAME
        )
    )


@pytest.mark.v0
@pytest.mark.v1
def test_relate_client_charm(juju_vm_model: Juju) -> None:
    """Test normal client charm relation."""
    juju_vm_model.integrate(APP_NAME, REQUIRER_NAME)
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(
            status, APP_NAME, REQUIRER_NAME, idle_period=10
        )
    )

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME, tls_enabled=True)
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)
    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    # check if user and role are created for the common name and that the role is assigned to the user
    common_names = get_requirer_common_names(juju_vm_model)
    logger.info(f"Requirer has common names: {common_names}")
    for common_name in common_names:
        user_roles = get_user(
            endpoints, common_name, user=INTERNAL_USER, password=password, tls_enabled=True
        )
        assert user_roles, f"failed to get user roles for {common_name}"
        assert common_name in user_roles, f"failed to get user roles for {common_name}"

        # check if the user can read and write to the key prefix
        permissions = get_role(
            endpoints, common_name, user=INTERNAL_USER, password=password, tls_enabled=True
        )

        assert permissions, f"failed to get permissions for {common_name}"
        for permission in permissions:
            assert permission["permType"] == 2, "permission is not read and write"
            if USER_WITH_FULL_KEYSPACE_ACCESS == common_name:
                assert permission["key"] == "\x00", "full keyspace access has not been granted"
            else:
                assert permission["key"] == f"/{common_name}/", (
                    "permission is not for the key prefix"
                )

    # get client ca from every unit and check if it includes the mtls cert
    mtls_certs = get_requirer_mtls_certificates(juju_vm_model)
    assert mtls_certs, "failed to get mtls cert from requirer TLS provider"
    for unit_name in juju_vm_model.status().get_units(APP_NAME):
        client_cas = get_certificate_from_unit(
            juju_vm_model, unit_name, TLSType.CLIENT, is_ca=True
        )
        assert client_cas, f"failed to get client CAs for {unit_name}"
        for mtls_cert in mtls_certs:
            assert mtls_cert in client_cas, f"mtls cert not in trusted CAs for {unit_name}"


@pytest.mark.v0
@pytest.mark.v1
def test_write_read_with_requirer(juju_vm_model: Juju) -> None:
    """Test write and read to the key prefix with the requirer charm."""
    requirer_unit = next(iter(juju_vm_model.status().get_units(REQUIRER_NAME)))
    common_names = get_requirer_common_names(juju_vm_model)

    for common_name in common_names:
        # write to the key prefix
        if USER_WITH_FULL_KEYSPACE_ACCESS == common_name:
            action = juju_vm_model.run(
                requirer_unit,
                "put",
                params={"key": TEST_KEY, "value": TEST_VALUE, "user": common_name},
            )
            assert action.status == "completed", "Action should succeed"
        else:
            with pytest.raises(TaskError) as task_error:
                juju_vm_model.run(
                    requirer_unit,
                    "put",
                    params={"key": TEST_KEY, "value": TEST_VALUE, "user": common_name},
                )
            assert "permission denied" in str(task_error), (
                "Action should fail because user does not have permission to write to the key prefix"
            )

        # write to authorized key prefix
        # every user will write the key to their own prefix
        key = f"/{common_name}/test/foo"
        action = juju_vm_model.run(
            requirer_unit, "put", params={"key": key, "value": TEST_VALUE, "user": common_name}
        )
        assert action.status == "completed", "Action should succeed"

        # read from the key prefix
        action = juju_vm_model.run(requirer_unit, "get", params={"key": key, "user": common_name})
        assert action.status == "completed", "Action should succeed"
        result = json.loads(action.results["result"])
        assert result == f"{key}\n{TEST_VALUE}"


@pytest.mark.v0
@pytest.mark.v1
def test_update_mtls_cert(juju_vm_model: Juju) -> None:
    """Test updating the common name used by the requirer app."""
    old_mtls_certs = get_requirer_mtls_certificates(juju_vm_model)
    assert old_mtls_certs, "failed to get the old mtls certs from requirer TLS provider"

    # run juju action to update the common name
    requirer_unit = next(iter(juju_vm_model.status().get_units(REQUIRER_NAME)))

    juju_vm_model.run(requirer_unit, "update-mtls-certs")

    # wait for model to settle
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(
            status, APP_NAME, REQUIRER_NAME, idle_period=10
        )
    )

    # get client ca from every unit and check if it includes the new_ca
    mtls_certs = get_requirer_mtls_certificates(juju_vm_model)
    assert mtls_certs, "failed to get the new mtls certs from requirer TLS provider"

    for unit_name in juju_vm_model.status().get_units(APP_NAME):
        client_cas = get_certificate_from_unit(
            juju_vm_model, unit_name, TLSType.CLIENT, is_ca=True
        )
        assert client_cas, f"failed to get client CAs for {unit_name}"
        for mtls_cert in mtls_certs:
            assert mtls_cert in client_cas, f"new mtls cert not in trusted CAs for {unit_name}"
        for old_mtls_cert in old_mtls_certs:
            assert old_mtls_cert not in client_cas, (
                f"old mtls certificate still in trusted CAs for {unit_name}"
            )


@pytest.mark.v0
@pytest.mark.v1
def test_etcd_updates_ca(juju_vm_model: Juju) -> None:
    """Update the common name used by the requirer app."""
    requirer_unit = next(iter(juju_vm_model.status().get_units(REQUIRER_NAME)))

    logger.debug("Getting current server ca")
    # write to the key prefix
    action = juju_vm_model.run(requirer_unit, "get-credentials")

    assert action.status == "completed", "Action should succeed"
    old_ca = action.results["tls-ca"]

    # Update common name on TLS provider for etcd and the client application
    logger.debug("Updating common name on TLS provider")

    juju_vm_model.config(app=TLS_NAME, values={"ca-common-name": "NEW_CN_CA"})

    # wait for model to settle
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, REQUIRER_NAME, TLS_NAME)
    )
    logger.debug("Getting new server ca")
    action = juju_vm_model.run(requirer_unit, "get-credentials")

    assert action.status == "completed", "Action should succeed"
    new_ca = action.results["tls-ca"]

    assert old_ca != new_ca, "CA should be updated"

    logger.info("Ensure updated mtls-certs are trusted on etcd")
    mtls_certs = get_requirer_mtls_certificates(juju_vm_model)
    assert mtls_certs, "failed to get the new mtls certs from requirer TLS provider"

    for unit_name in juju_vm_model.status().get_units(APP_NAME):
        client_cas = get_certificate_from_unit(
            juju_vm_model, unit_name, TLSType.CLIENT, is_ca=True
        )
        for mtls_cert in mtls_certs:
            assert mtls_cert in client_cas, f"new mtls cert not in trusted CAs for {unit_name}"


@pytest.mark.v0
@pytest.mark.v1
def test_remove_client_relation(juju_vm_model: Juju) -> None:
    """Test removing the client relation and check if the user and role are removed."""
    mtls_certs = get_requirer_mtls_certificates(juju_vm_model)
    assert mtls_certs, "failed to get mtls certs from requirer TLS provider"

    # get common names from requirer
    logger.debug("Getting common names from requirer")
    requirer_unit = next(iter(juju_vm_model.status().get_units(REQUIRER_NAME)))

    action = juju_vm_model.run(requirer_unit, "get-credentials")

    assert action.status == "completed", "Action should succeed"
    common_names = action.results["username"].split(",")
    assert common_names, "failed to get common names from requirer"

    logger.info("Removing client relation")
    juju_vm_model.remove_relation(APP_NAME, REQUIRER_NAME)

    # wait for model to settle
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, REQUIRER_NAME)
    )

    # check that the user and role are removed
    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME, tls_enabled=True)
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)

    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    for common_name in common_names:
        user_roles = get_user(
            endpoints, common_name, user=INTERNAL_USER, password=password, tls_enabled=True
        )
        assert user_roles is None, "user still exist"
        # check if the user can read and write to the key prefix
        permissions = get_role(
            endpoints, common_name, user=INTERNAL_USER, password=password, tls_enabled=True
        )
        assert permissions is None, "role still exist"

    # get client ca from every unit and check if it includes the test_ca
    for unit_name in juju_vm_model.status().get_units(APP_NAME):
        client_cas = get_certificate_from_unit(
            juju_vm_model, unit_name, TLSType.CLIENT, is_ca=True
        )
        assert client_cas, f"failed to get client CAs for {unit_name}"
        for mtls_cert in mtls_certs:
            assert mtls_cert not in client_cas, (
                f"old mtls cert still in trusted CAs for {unit_name}"
            )


@pytest.mark.v0
@pytest.mark.v1
def test_different_tls_providers(juju_vm_model: Juju) -> None:
    """Ensure a CA rotation also works when using separate TLS providers."""
    logger.info("Remove TLS relation for requirer.")
    juju_vm_model.remove_relation(f"{REQUIRER_NAME}:certificates", f"{TLS_NAME}:certificates")
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, REQUIRER_NAME, idle_period=10)
    )

    logger.info("Integrate requirer with different TLS provider.")
    juju_vm_model.integrate(REQUIRER_NAME, REQUIRER_TLS_NAME)
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(
            status, APP_NAME, REQUIRER_NAME, idle_period=10
        )
    )

    logger.info("Integrate requirer with etcd again.")
    juju_vm_model.integrate(APP_NAME, REQUIRER_NAME)
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(
            status, APP_NAME, REQUIRER_NAME, idle_period=10
        )
    )
    # Update common name on TLS provider for client application
    logger.info("Updating common name on TLS provider")
    juju_vm_model.config(REQUIRER_TLS_NAME, {"ca-common-name": "EVEN_NEWER_CA"})

    # wait for model to settle
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, REQUIRER_NAME, TLS_NAME)
    )

    logger.info("Ensure updated mtls-certs are trusted on etcd")
    mtls_certs = get_requirer_mtls_certificates(juju_vm_model)
    assert mtls_certs, "failed to get the new mtls certs from requirer TLS provider"

    for unit_name in juju_vm_model.status().get_units(APP_NAME):
        client_cas = get_certificate_from_unit(
            juju_vm_model, unit_name, TLSType.CLIENT, is_ca=True
        )
        for mtls_cert in mtls_certs:
            assert mtls_cert in client_cas, f"new mtls cert not in trusted CAs for {unit_name}"

    logger.info("Removing client relation")
    juju_vm_model.remove_relation(
        f"{REQUIRER_NAME}:{EXTERNAL_CLIENTS_RELATION}", f"{APP_NAME}:{EXTERNAL_CLIENTS_RELATION}"
    )

    # wait for model to settle
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, REQUIRER_NAME)
    )


@pytest.mark.v0
@pytest.mark.v1
def test_certificate_transfer(juju_vm_model: Juju) -> None:
    """Test if the certificate transfer interface works correctly."""
    # integrate etcd with requirer tls provider on certificate_transfer relation
    juju_vm_model.integrate(f"{APP_NAME}:client-cas", REQUIRER_TLS_NAME)

    # wait for model to settle
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, REQUIRER_TLS_NAME)
    )

    # get ca from the REQUIRER_TLS_NAME
    requirer_tls_unit = next(iter(juju_vm_model.status().get_units(REQUIRER_TLS_NAME)))
    action = juju_vm_model.run(requirer_tls_unit, "get-ca-certificate")
    ca_cert = action.results["ca-certificate"]
    assert ca_cert, "failed to get ca certificate from requirer tls provider"

    # get client ca from every unit and check if it includes the ca_cert
    for unit_name in juju_vm_model.status().get_units(APP_NAME):
        client_cas = get_certificate_from_unit(
            juju_vm_model, unit_name, TLSType.CLIENT, is_ca=True
        )
        assert client_cas, f"failed to get client CAs for {unit_name}"
        assert ca_cert in client_cas, f"CA chain not in trusted CAs for {unit_name}"


@pytest.mark.v0
@pytest.mark.v1
def test_requirer_sends_ca(juju_vm_model: Juju) -> None:
    """Test when the requirer charm sends a ca certificate instead of an end-entity."""
    # configure the requirer charm to send a ca certificate
    juju_vm_model.config(REQUIRER_NAME, {"send-ca-cert": "True"})

    # integrate the requirer charm
    juju_vm_model.integrate(APP_NAME, REQUIRER_NAME)

    # wait for model to settle
    juju_vm_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(
                    app_status=[ExternalClientsStatuses.EC_INVALID_CERTIFICATE.value],
                    unit_status=[ExternalClientsStatuses.EC_INVALID_CERTIFICATE.value],
                ),
                REQUIRER_NAME: ExpectedStatus(
                    app_status=[CharmStatuses.ACTIVE_IDLE.value],
                    unit_status=[CharmStatuses.ACTIVE_IDLE.value],
                ),
                TLS_NAME: ExpectedStatus(
                    app_status=[CharmStatuses.ACTIVE_IDLE.value],
                    unit_status=[CharmStatuses.ACTIVE_IDLE.value],
                ),
            },
        )
    )
