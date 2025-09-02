#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from platform import machine
from time import sleep

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION, TLSType
from statuses import CharmStatuses, TLSStatuses

from ..helpers import (
    APP_NAME,
    TLS_NAME,
    get_certificate_from_unit,
    get_cluster_endpoints,
    get_cluster_members,
    get_etcd_version,
    get_remaining_endpoints,
    get_secret_by_label,
    get_unit_endpoint,
)
from ..helpers_deployment import wait_until
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    start_continuous_writes,
    stop_continuous_writes,
)
from .helpers_network import (
    cut_network_from_unit_with_ip_change,
    get_controller_hostname,
    hostname_from_unit,
    ip_address_from_unit,
    is_unit_reachable,
    restore_network_for_unit_with_ip_change,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3
CHARM_CHANNEL = "3.6/edge"
CHARM_REVISIONS_TO_DEPLOY = {"x86_64": 89, "aarch64": 88}
WORKLOAD_VERSION = {"previous": "3.6.1", "target": "3.6.2"}
CERTIFICATE_EXPIRY_TIME = 250


@pytest.mark.abort_on_fail
async def test_upgrade_single_unit_cluster(charm: str, ops_test: OpsTest) -> None:
    """Deploy one unit of etcd and upgrade it - without HA."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=1,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME])

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await etcd_application.units[0].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME])
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd version")
    unit_endpoint = get_unit_endpoint(
        ops_test, unit_name=etcd_application.units[0].name, app_name=APP_NAME
    )
    assert (
        get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
        == WORKLOAD_VERSION["target"]
    ), "etcd was not upgraded"

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)


@pytest.mark.abort_on_fail
async def test_disaster_recovery_during_upgrade(charm: str, ops_test: OpsTest) -> None:
    """Recover a failed cluster of two units during an upgrade."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=2,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=2)

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(apps=[APP_NAME], wait_for_exact_units=2, idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await etcd_application.units[-1].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )

    await ops_test.model.wait_for_idle(apps=[APP_NAME], wait_for_exact_units=2, idle_period=30)

    for unit in etcd_application.units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    logger.info("Rebuilding cluster to simulate recovery after majority failure")
    rebuild_action = await leader_unit.run_action("rebuild-cluster", **{"force": True})
    rebuild_response = await rebuild_action.wait()
    assert rebuild_response.results.get("return-code") == 0, "rebuild failed"

    await ops_test.model.wait_for_idle(apps=[APP_NAME], wait_for_exact_units=2)

    # cluster should be recovered again
    assert "Cluster failure" not in etcd_application.units[-1].workload_status_message, (
        "Cluster could not be recovered"
    )
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    for unit in etcd_application.units:
        assert any(unit.name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit.name} is not in {cluster_members}"
        )
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    assert "resume-refresh" in etcd_application.status_message, (
        "Refresh should wait for user to continue with `resume-refresh` action"
    )
    logger.info("Continue refresh on all other units with `resume-refresh` action")
    resume_refresh_action = await etcd_application.units[0].run_action("resume-refresh")
    resume_refresh_response = await resume_refresh_action.wait()
    assert resume_refresh_response.results.get("return-code") == 0, "action failed"

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=2)
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd versions and cluster membership")

    for unit in etcd_application.units:
        unit_endpoint = get_unit_endpoint(ops_test, unit_name=unit.name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit.name} was not upgraded"

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)


@pytest.mark.abort_on_fail
async def test_ip_address_change_during_upgrade(charm: str, ops_test: OpsTest) -> None:
    """Process an updated ip address during an upgrade."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        etcd_application.units,
        key=lambda unit: int(unit.name.split("/")[1]),
        reverse=True,
    )

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        wait_for_exact_units=NUM_UNITS,
        idle_period=30,
    )

    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info(f"Continue refresh on unit {refresh_order[0].name}")
        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        force_refresh_action = await refresh_order[0].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )
        force_refresh_response = await force_refresh_action.wait()
        assert force_refresh_response.results.get("return-code") == 0, "action failed"

    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["blocked"])

    logger.info(f"Force new ip address for {refresh_order[-1].name}")
    ip_renewal_hostname = await hostname_from_unit(ops_test, unit_name=refresh_order[-1].name)
    old_unit_ip = await ip_address_from_unit(ops_test, unit_name=refresh_order[-1].name)
    old_unit_endpoint = get_unit_endpoint(ops_test, unit_name=refresh_order[-1].name)
    cut_network_from_unit_with_ip_change(ip_renewal_hostname)

    # make sure the unit is not reachable from the controller
    controller_hostname = await get_controller_hostname(ops_test)
    assert not is_unit_reachable(controller_hostname, ip_renewal_hostname)
    logger.info(f"{refresh_order[-1].name} is not reachable via network.")

    # as the stopped member is unresponsive, only query the endpoints still available
    remaining_endpoints = get_remaining_endpoints(endpoints, old_unit_endpoint)
    assert_continuous_writes_increasing(
        endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
    )

    # reconnect the network for the disconnected unit
    restore_network_for_unit_with_ip_change(ip_renewal_hostname)
    logger.info(f"Network has been restored for {refresh_order[-1].name}")

    await wait_until(
        ops_test,
        apps=[APP_NAME],
        apps_statuses=["blocked"],
        units_statuses=["active"],
        wait_for_exact_units=NUM_UNITS,
    )

    # ensure the unit is up again
    new_unit_ip = await ip_address_from_unit(ops_test, unit_name=refresh_order[-1].name)
    logger.info(f"{refresh_order[-1].name} is available again with new ip {new_unit_ip}")

    logger.info("Continue refresh with `resume-refresh` action")
    resume_refresh_action = await refresh_order[1].run_action("resume-refresh")
    resume_refresh_response = await resume_refresh_action.wait()
    assert resume_refresh_response.results.get("return-code") == 0, "action failed"

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)
    endpoints_updated = endpoints.replace(old_unit_ip, new_unit_ip)
    assert_continuous_writes_increasing(
        endpoints=endpoints_updated, user=INTERNAL_USER, password=password
    )

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints_updated, user=INTERNAL_USER, password=password)
    for unit in etcd_application.units:
        unit_endpoint = get_unit_endpoint(ops_test, unit_name=unit.name, app_name=APP_NAME)
        # workaround in case ip address was not updated in `unit.public_address`
        unit_endpoint.replace(old_unit_ip, new_unit_ip)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit.name} was not upgraded"

        assert any(unit.name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit.name} is not in {cluster_members}"
        )

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=endpoints_updated, user=INTERNAL_USER, password=password
    )
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)


@pytest.mark.abort_on_fail
async def test_tls_cert_rotation_during_upgrade(charm: str, ops_test: OpsTest) -> None:
    """Process new TLS certificates during an upgrade."""
    await ops_test.model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd", "certificate-validity": "3m"}
    await ops_test.model.deploy(TLS_NAME, channel="1/edge", config=tls_config)

    await wait_until(ops_test, apps=[APP_NAME, TLS_NAME])

    etcd_application = ops_test.model.applications[APP_NAME]
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Integrating peer-certificates relation")
    await ops_test.model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)

    await wait_until(
        ops_test,
        apps=[APP_NAME, TLS_NAME],
        units_full_statuses={
            APP_NAME: [TLSStatuses.TLS_PEER_CERTS_EXPIRING.value],
            TLS_NAME: [CharmStatuses.ACTIVE_IDLE.value],
        },
    )

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    await etcd_application.refresh(path=charm)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        etcd_application.units,
        key=lambda unit: int(unit.name.split("/")[1]),
        reverse=True,
    )

    logger.info("Getting current certificate from leader unit")
    current_peer_certificate = get_certificate_from_unit(
        ops_test.model_full_name, refresh_order[-1], cert_type=TLSType.PEER
    )
    assert current_peer_certificate, "Failed to get current peer certificate"

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30)
    if "incompatible" in etcd_application.status_message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        await etcd_application.units[0].run_action(
            "force-refresh-start", **{"check-compatibility": False}
        )

    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["blocked"])

    # wait for certificate to expire
    logger.info("Waiting for certificate to expire")
    sleep(CERTIFICATE_EXPIRY_TIME)

    new_peer_certificate = get_certificate_from_unit(
        ops_test.model_full_name, refresh_order[-1], cert_type=TLSType.PEER
    )
    assert new_peer_certificate, "Failed to get new peer certificate"
    assert new_peer_certificate != current_peer_certificate, (
        "Certificates are the same after rotation"
    )

    logger.info("Continue refresh with `resume-refresh` action")
    resume_refresh_action = await refresh_order[1].run_action("resume-refresh")
    resume_refresh_response = await resume_refresh_action.wait()
    assert resume_refresh_response.results.get("return-code") == 0, "action failed"

    # wait for upgrade to complete
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    for unit in etcd_application.units:
        unit_endpoint = get_unit_endpoint(ops_test, unit_name=unit.name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit.name} was not upgraded"

        assert any(unit.name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit.name} is not in {cluster_members}"
        )

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)
    await ops_test.model.remove_application(TLS_NAME, block_until_done=True)
