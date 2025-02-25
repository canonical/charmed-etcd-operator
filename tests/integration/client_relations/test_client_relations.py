#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import shutil
import subprocess

import pytest
from juju.application import Application
from juju.unit import Unit
from pytest_operator.plugin import OpsTest

from literals import EXTERNAL_CLIENTS_RELATION, INTERNAL_USER, PEER_RELATION, TLSType

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

common_name = REQUIRER_NAME
key_prefix = "/test/"
# ca_chain = "-----BEGIN CERTIFICATE-----\ntest_ca\n-----END CERTIFICATE-----"


async def get_requirer_common_name(ops_test: OpsTest) -> str:
    """Get the common name of the requirer charm."""
    requirer_app = ops_test.model.applications[REQUIRER_NAME]
    requirer_unit = requirer_app.units[0]

    command = f'juju ssh {requirer_unit.name} "cat /var/lib/juju/agents/unit-{requirer_unit.name.replace("/", "-")}/charm/tmp/common_name.txt"'
    result = subprocess.getoutput(command)
    return result.strip()


async def get_requirer_ca_chain(ops_test: OpsTest) -> str | None:
    """Get the ca chain from the requirer TLS provider."""
    requirer_tls_app = ops_test.model.applications[REQUIRER_TLS_NAME]
    requirer_tls_unit = requirer_tls_app.units[0]

    result = await requirer_tls_unit.run_action("get-ca-certificate")
    result = await result.wait()
    if result.status:
        return result.results["ca-certificate"]

    return None


@pytest.fixture
async def application_charm(ops_test: OpsTest):
    """Build the application charm."""
    shutil.copyfile(
        "./lib/charms/data_platform_libs/v0/data_interfaces.py",
        "./tests/integration/client_relations/requirer-charm/lib/charms/data_platform_libs/v0/data_interfaces.py",
    )
    test_charm_path = "tests/integration/client_relations/requirer-charm"
    return await ops_test.build_charm(test_charm_path)


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, application_charm) -> None:
    """Build and deploy the charm-under-test and the requirer charm."""
    tls_config = {"ca-common-name": "etcd"}
    await asyncio.gather(
        ops_test.model.deploy(application_charm, application_name=REQUIRER_NAME),
        ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS),
        ops_test.model.deploy(TLS_NAME, channel="edge", config=tls_config),
        ops_test.model.deploy(
            TLS_NAME, application_name=REQUIRER_TLS_NAME, channel="edge", config=tls_config
        ),
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
async def test_get_common_name(ops_test: OpsTest) -> None:
    """Get the common name of the requirer charm."""
    common_name = await get_requirer_common_name(ops_test)
    assert common_name == REQUIRER_NAME, "common name is not correct"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_relate_client_charm(ops_test: OpsTest) -> None:
    """Relate the client charm."""
    await ops_test.model.integrate(APP_NAME, REQUIRER_NAME)
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME])

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    await download_client_certificate_from_unit(ops_test, APP_NAME)

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    # check if user and role are created for the common name and that the role is assigned to the user
    common_name = await get_requirer_common_name(ops_test)
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

    # get client ca from every unit and check if it includes the test_ca

    model = ops_test.model_full_name
    ca_chain = await get_requirer_ca_chain(ops_test)
    assert ca_chain, "failed to get ca chain from requirer TLS provider"
    for unit in ops_test.model.applications[APP_NAME].units:
        client_cas = get_certificate_from_unit(model, unit.name, TLSType.CLIENT, is_ca=True)
        assert client_cas, f"failed to get client CAs for {unit.name}"
        assert ca_chain in client_cas, f"CA chain not in trusted CAs for {unit.name}"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_update_common_name(ops_test: OpsTest) -> None:
    """Update the common name used by the requirer app."""
    new_common_name = "new-common-name"
    # run juju action to update the common name
    requirer_unit: Unit = ops_test.model.applications[REQUIRER_NAME].units[0]
    action = await requirer_unit.run_action("update", **{"common-name": new_common_name})
    action = await action.wait()

    # wait for model to settle
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME, REQUIRER_TLS_NAME])

    endpoints = get_cluster_endpoints(ops_test, APP_NAME, tls_enabled=True)
    await download_client_certificate_from_unit(ops_test, APP_NAME)

    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret, f"failed to get secret for {PEER_RELATION}.{APP_NAME}.app"
    password = secret.get(f"{INTERNAL_USER}-password")

    user_roles = get_user(
        endpoints, new_common_name, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert user_roles, f"failed to get user roles for {new_common_name}"

    assert new_common_name in user_roles, f"failed to get user roles for {new_common_name}"

    # check if the user can read and write to the key prefix
    permissions = get_role(
        endpoints, new_common_name, user=INTERNAL_USER, password=password, tls_enabled=True
    )

    assert permissions, f"failed to get permissions for {new_common_name}"

    for permission in permissions:
        assert permission["permType"] == 2, "permission is not read and write"
        assert permission["key"] == key_prefix, "permission is not for the key prefix"

    # verify that the old common name is not present
    user_roles = get_user(
        endpoints, common_name, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert user_roles is None, "old common name still exists"

    permissions = get_role(
        endpoints, common_name, user=INTERNAL_USER, password=password, tls_enabled=True
    )
    assert permissions is None, "old role still exist"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_update_ca(ops_test: OpsTest) -> None:
    """Update the common name used by the requirer app."""
    new_ca = "---BEGIN CERTIFICATE---\nnew_ca\n---END CERTIFICATE---"
    # run juju action to update the common name
    requirer_unit: Unit = ops_test.model.applications[REQUIRER_NAME].units[0]
    action = await requirer_unit.run_action("update", **{"ca": new_ca})
    action = await action.wait()

    # wait for model to settle
    await wait_until(ops_test, apps=[APP_NAME, REQUIRER_NAME])

    # get client ca from every unit and check if it includes the test_ca
    model = ops_test.model_full_name
    assert ops_test.model
    assert ops_test.model.applications[APP_NAME] is not None
    ca_chain = await get_requirer_ca_chain(ops_test)
    assert ca_chain, "failed to get ca chain from requirer TLS provider"
    for unit in ops_test.model.applications[APP_NAME].units:
        client_cas = get_certificate_from_unit(model, unit.name, TLSType.CLIENT, is_ca=True)
        assert client_cas, f"failed to get client CAs for {unit.name}"
        assert new_ca in client_cas, f"CA chain not in trusted CAs for {unit.name}"
        assert ca_chain not in client_cas, f"old CA chain still in trusted CAs for {unit.name}"


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy", "large"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_remove_client_relation(ops_test: OpsTest) -> None:
    """Remove the client relation and check if the user and role are removed."""
    common_name = "new-common-name"
    ca_chain = "---BEGIN CERTIFICATE---\nnew_ca\n---END CERTIFICATE---"
    etcd_app: Application = ops_test.model.applications[APP_NAME]  # type: ignore

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
