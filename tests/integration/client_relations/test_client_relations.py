#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import shutil
import subprocess
from datetime import timedelta

import pytest
from charms.tls_certificates_interface.v4.tls_certificates import (
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)
from juju.application import Application
from juju.unit import Unit
from pytest_operator.plugin import OpsTest

from literals import EXTERNAL_CLIENTS_RELATION, INTERNAL_USER, PEER_RELATION, Status, TLSType

from ..helpers import (
    APP_NAME,
    CHARM_PATH,
    TLS_NAME,
    download_client_certificate_from_unit,
    get_certificate_from_unit,
    get_cluster_endpoints,
    get_role,
    get_secret_by_label,
    get_user,
)
from ..helpers_deployment import wait_until

logger = logging.getLogger(__name__)

NUM_UNITS = 3
TEST_KEY = "test_key"
TEST_VALUE = "42"
REQUIRER_NAME = "requirer-charm"
REQUIRER_TLS_NAME = "requirer-tls-provider"
REQUIRER_CHARM_PATH = "./requirer-charm_ubuntu@24.04-amd64.charm"

common_name = REQUIRER_NAME
key_prefix = "/test/"


def generate_mtls_chain(common_name: str) -> str:
    ca_private_key = generate_private_key()
    ca_cert = generate_ca(
        private_key=ca_private_key, validity=timedelta(days=365), common_name="ca_common_name"
    )

    client_private_key = generate_private_key()
    client_csr = generate_csr(private_key=client_private_key, common_name=common_name)
    client_cert = generate_certificate(
        client_csr, ca_cert, ca_private_key, validity=timedelta(days=365)
    )
    return "\n".join([client_cert.raw, ca_cert.raw])


async def get_requirer_common_name(ops_test: OpsTest) -> str:
    """Get the common name of the requirer charm."""
    requirer_app = ops_test.model.applications[REQUIRER_NAME]
    requirer_unit = requirer_app.units[0]

    command = f'juju ssh {requirer_unit.name} "cat /var/lib/juju/agents/unit-{requirer_unit.name.replace("/", "-")}/charm/tmp/common_name.txt"'
    result = subprocess.getoutput(command).strip()
    if "Connection" in result:
        return result.split("Connection")[0].strip()
    return result


async def get_requirer_leaf_certificate(ops_test: OpsTest) -> str | None:
    """Get the ca chain from the requirer TLS provider."""
    requirer_app: Application = ops_test.model.applications[REQUIRER_NAME]
    requirer_unit: Unit = requirer_app.units[0]

    action = await requirer_unit.run_action("get-certificate")
    result = await action.wait()
    if result.status:
        return result.results["certificate"]

    return None


@pytest.fixture
def application_charm():
    """Build the application charm."""
    shutil.copyfile(
        "./lib/charms/data_platform_libs/v0/data_interfaces.py",
        "./tests/integration/client_relations/requirer-charm/lib/charms/data_platform_libs/v0/data_interfaces.py",
    )
    test_charm_path = "tests/integration/client_relations/requirer-charm"
    logger.info("Building the requirer charm")
    subprocess.check_output(
        [
            "charmcraft",
            "pack",
            "-p",
            test_charm_path,
        ],
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, application_charm) -> None:
    """Build and deploy the charm-under-test and the requirer charm."""
    tls_config = {"ca-common-name": "etcd"}
    await asyncio.gather(
        ops_test.model.deploy(REQUIRER_CHARM_PATH, application_name=REQUIRER_NAME),
        ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS),
        ops_test.model.deploy(TLS_NAME, config=tls_config),
        ops_test.model.deploy(TLS_NAME, application_name=REQUIRER_TLS_NAME, config=tls_config),
    )
    # enable TLS and check if the cluster is still accessible
    logger.info("Integrating peer-certificates and client-certificates relations")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)
    await ops_test.model.integrate(f"{APP_NAME}:client-certificates", TLS_NAME)
    await ops_test.model.integrate(REQUIRER_NAME, REQUIRER_TLS_NAME)
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME, TLS_NAME, REQUIRER_TLS_NAME])


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_relate_client_charm(ops_test: OpsTest) -> None:
    """Relate the client charm."""
    await ops_test.model.integrate(APP_NAME, REQUIRER_NAME)
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME], idle_period=10)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    await download_client_certificate_from_unit(ops_test, APP_NAME)

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    # check if user and role are created for the common name and that the role is assigned to the user
    common_name = await get_requirer_common_name(ops_test)
    logger.info(f"Requirer has common name: {common_name}")
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
        assert permission["key"] == key_prefix, "permission is not for the key prefix"

    # get client ca from every unit and check if it includes the ca_chain

    model = ops_test.model_full_name
    ca_chain = await get_requirer_leaf_certificate(ops_test)
    assert ca_chain, "failed to get ca chain from requirer TLS provider"
    for unit in ops_test.model.applications[APP_NAME].units:
        client_cas = get_certificate_from_unit(model, unit.name, TLSType.CLIENT, is_ca=True)
        assert client_cas, f"failed to get client CAs for {unit.name}"
        assert ca_chain in client_cas, f"CA chain not in trusted CAs for {unit.name}"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_write_read_with_requirer(ops_test: OpsTest) -> None:
    """Write and read to the key prefix with the requirer charm."""
    requirer_app: Application = ops_test.model.applications[REQUIRER_NAME]
    requirer_unit: Unit = requirer_app.units[0]

    # write to the key prefix
    action = await requirer_unit.run_action("put", **{"key": TEST_KEY, "value": TEST_VALUE})
    action = await action.wait()

    assert action.status == "failed" and "permission denied" in action.results["stderr"], (
        "Action should fail because user does not have permission to write to the key prefix"
    )

    # write to authorized key prefix
    key = "/test/foo"
    action = await requirer_unit.run_action("put", **{"key": key, "value": TEST_VALUE})
    action = await action.wait()
    assert action.status == "completed", "Action should succeed"

    # read from the key prefix
    action = await requirer_unit.run_action("get", **{"key": key})
    action = await action.wait()
    assert action.status == "completed", "Action should succeed"
    assert action.results["message"] == f"{key}\n{TEST_VALUE}", "Action should return the value"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_update_chain(ops_test: OpsTest) -> None:
    """Update the common name used by the requirer app."""
    # generate new ca chain
    new_ca = generate_mtls_chain("new-common-name")
    # run juju action to update the common name
    requirer_unit: Unit = ops_test.model.applications[REQUIRER_NAME].units[0]
    action = await requirer_unit.run_action("update", **{"chain": new_ca})
    action = await action.wait()

    # wait for model to settle
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME], idle_period=10)

    # get client ca from every unit and check if it includes the new_ca
    model = ops_test.model_full_name
    assert ops_test.model
    assert ops_test.model.applications[APP_NAME] is not None

    # get common name from the requirer charm
    common_name = await get_requirer_common_name(ops_test)
    assert common_name == "new-common-name", "common name not updated"

    ca_chain = await get_requirer_leaf_certificate(ops_test)
    assert ca_chain, "failed to get ca chain from requirer TLS provider"
    for unit in ops_test.model.applications[APP_NAME].units:
        client_cas = get_certificate_from_unit(model, unit.name, TLSType.CLIENT, is_ca=True)
        assert client_cas, f"failed to get client CAs for {unit.name}"
        assert new_ca in client_cas, f"CA chain not in trusted CAs for {unit.name}"
        assert ca_chain not in client_cas, f"old CA chain still in trusted CAs for {unit.name}"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_etcd_updates_ca(ops_test: OpsTest) -> None:
    """Update the common name used by the requirer app."""
    requirer_app: Application = ops_test.model.applications[REQUIRER_NAME]
    requirer_unit: Unit = requirer_app.units[0]

    logger.debug("Getting current server ca")
    # write to the key prefix
    action = await requirer_unit.run_action("get-credentials")
    action = await action.wait()

    assert action.status == "completed", "Action should succeed"
    old_ca = action.results["tls-ca"]

    # Update common name on TLS provider for etcd
    logger.debug("Updating common name on TLS provider")

    etcd_tls_operator: Application = ops_test.model.applications[TLS_NAME]
    await etcd_tls_operator.set_config({"ca-common-name": "NEW_CN_CA"})

    # wait for model to settle
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME, TLS_NAME])

    logger.debug("Getting new server ca")
    action = await requirer_unit.run_action("get-credentials")
    action = await action.wait()

    assert action.status == "completed", "Action should succeed"
    new_ca = action.results["tls-ca"]

    assert old_ca != new_ca, "CA should be updated"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_remove_client_relation(ops_test: OpsTest) -> None:
    """Remove the client relation and check if the user and role are removed."""
    common_name = "new-common-name"
    ca_chain = await get_requirer_leaf_certificate(ops_test)
    assert ca_chain, "failed to get ca chain from requirer TLS provider"
    etcd_app: Application = ops_test.model.applications[APP_NAME]

    logger.info("Removing client relation")
    await etcd_app.remove_relation(
        EXTERNAL_CLIENTS_RELATION, f"{REQUIRER_NAME}:{EXTERNAL_CLIENTS_RELATION}"
    )

    # wait for model to settle
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME])

    # check that the user and role are removed
    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    await download_client_certificate_from_unit(ops_test, APP_NAME)

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

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
    model = ops_test.model_full_name
    assert ops_test.model.applications[APP_NAME] is not None
    for unit in ops_test.model.applications[APP_NAME].units:
        client_cas = get_certificate_from_unit(model, unit.name, TLSType.CLIENT, is_ca=True)
        assert client_cas, f"failed to get client CAs for {unit.name}"
        assert ca_chain not in client_cas, f"old CA chain still in trusted CAs for {unit.name}"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_requirer_sends_ca(ops_test: OpsTest) -> None:
    """Test if the requirer charm sends a ca certificate instead of an end-entity."""
    # configure the requirer charm to send a ca certificate
    requirer_app: Application = ops_test.model.applications[REQUIRER_NAME]
    await requirer_app.set_config({"send-ca-cert": "True"})
    # integrate the requirer charm
    await ops_test.model.integrate(APP_NAME, REQUIRER_NAME)

    # wait for model to settle
    await wait_until(
        ops_test,
        apps=[APP_NAME, REQUIRER_NAME],
        # idle_period=10,
        apps_full_statuses={
            APP_NAME: {
                "blocked": [Status.EC_INVALID_CERTIFICATE.value.status.message],
            },
            REQUIRER_NAME: {"active": []},
            TLS_NAME: {"active": []},
        },
        units_full_statuses={
            APP_NAME: {"units": {"blocked": [Status.EC_INVALID_CERTIFICATE.value.status.message]}},
            REQUIRER_NAME: {"units": {"active": []}},
            TLS_NAME: {"units": {"active": []}},
        },
    )
