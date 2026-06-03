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
from jubilant import Juju, TaskError, temp_model

from literals import INTERNAL_USER, PEER_RELATION, TLSType

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
from ..helpers_deployment import are_apps_active_and_agents_idle

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "/test_key"
TEST_VALUE = "42"
REQUIRER_NAME = "requirer-charm"
USER_WITH_FULL_KEYSPACE_ACCESS = "client1.requirer-charm"


@pytest.fixture
def requirer_charm(arch: str) -> str:
    """Path to the requirer charm file to use for testing."""
    return f"./tests/integration/client_relations/requirer-charm/requirer-charm_ubuntu@24.04-{arch}.charm"


@pytest.fixture(scope="module")
def requirer_model(juju: Juju, lxd_cloud: str, lxd_controller: str, arch: str):
    with temp_model(cloud=lxd_cloud, controller=lxd_controller) as req_model:
        req_model.wait_timeout = 1000
        req_model.cli("set-model-constraints", f"arch={arch}")
        yield req_model


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
    charm: str,
    requirer_charm: str,
    juju_vm_model: Juju,
    requirer_model: Juju,
    data_interfaces_version: str,
) -> None:
    """Build and deploy the charm-under-test and the requirer charm."""
    tls_config = {"ca-common-name": "etcd"}
    requirer_model.deploy(
        requirer_charm,
        app=REQUIRER_NAME,
        config={"data-interfaces-version": data_interfaces_version},
    )
    juju_vm_model.deploy(charm, num_units=NUM_UNITS)
    juju_vm_model.deploy(TLS_NAME, channel="1/edge", config=tls_config)

    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    juju_vm_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    juju_vm_model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)

    logger.info("Integrate requirer with TLS provider cross-model")
    juju_vm_model.offer(app=TLS_NAME, endpoint="certificates")
    requirer_model.integrate(REQUIRER_NAME, f"{juju_vm_model.model.split(':')[1]}.{TLS_NAME}")
    juju_vm_model.wait(lambda status: are_apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))
    requirer_model.wait(lambda status: are_apps_active_and_agents_idle(status, REQUIRER_NAME))


# @pytest.mark.v0
@pytest.mark.v1
def test_relate_client_charm(juju_vm_model: Juju, requirer_model: Juju) -> None:
    """Test normal client charm relation."""
    logger.info("Integrate requirer with etcd cross-model")
    juju_vm_model.offer(app=APP_NAME, endpoint="etcd-client")
    requirer_model.integrate(REQUIRER_NAME, f"{juju_vm_model.model.split(':')[1]}.{APP_NAME}")
    requirer_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, REQUIRER_NAME, idle_period=30)
    )
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, idle_period=30)
    )

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME, tls_enabled=True)
    download_client_certificate_from_unit(juju_vm_model, APP_NAME)
    secret = get_secret_by_label(juju_vm_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    # check if user and role are created for the common name and that the role is assigned to the user
    common_names = get_requirer_common_names(requirer_model)
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
    mtls_certs = get_requirer_mtls_certificates(requirer_model)
    assert mtls_certs, "failed to get mtls cert from requirer TLS provider"
    for unit_name in juju_vm_model.status().get_units(APP_NAME):
        client_cas = get_certificate_from_unit(
            juju_vm_model, unit_name, TLSType.CLIENT, is_ca=True
        )
        assert client_cas, f"failed to get client CAs for {unit_name}"
        for mtls_cert in mtls_certs:
            assert mtls_cert in client_cas, f"mtls cert not in trusted CAs for {unit_name}"


# @pytest.mark.v0
@pytest.mark.v1
def test_write_read_with_requirer(juju_vm_model: Juju, requirer_model: Juju) -> None:
    """Test write and read to the key prefix with the requirer charm."""
    requirer_unit = next(iter(requirer_model.status().get_units(REQUIRER_NAME)))
    common_names = get_requirer_common_names(requirer_model)

    for common_name in common_names:
        # write to the key prefix
        if USER_WITH_FULL_KEYSPACE_ACCESS == common_name:
            action = requirer_model.run(
                requirer_unit,
                "put",
                params={"key": TEST_KEY, "value": TEST_VALUE, "user": common_name},
            )
            assert action.status == "completed", "Action should succeed"
        else:
            with pytest.raises(TaskError) as task_error:
                requirer_model.run(
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
        action = requirer_model.run(
            requirer_unit, "put", params={"key": key, "value": TEST_VALUE, "user": common_name}
        )
        assert action.status == "completed", "Action should succeed"

        # read from the key prefix
        action = requirer_model.run(requirer_unit, "get", params={"key": key, "user": common_name})
        assert action.status == "completed", "Action should succeed"
        result = json.loads(action.results["result"])
        assert result == f"{key}\n{TEST_VALUE}"


# @pytest.mark.v0
@pytest.mark.v1
def test_update_mtls_cert(juju_vm_model: Juju, requirer_model: Juju) -> None:
    """Test updating the common name used by the requirer app."""
    old_mtls_certs = get_requirer_mtls_certificates(requirer_model)
    assert old_mtls_certs, "failed to get the old mtls certs from requirer TLS provider"

    # run juju action to update the common name
    requirer_unit = next(iter(requirer_model.status().get_units(REQUIRER_NAME)))
    requirer_model.run(requirer_unit, "update-mtls-certs")

    # wait for models to settle
    requirer_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, REQUIRER_NAME, idle_period=30)
    )
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, idle_period=30)
    )

    # get client ca from every unit and check if it includes the new_ca
    mtls_certs = get_requirer_mtls_certificates(requirer_model)
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
