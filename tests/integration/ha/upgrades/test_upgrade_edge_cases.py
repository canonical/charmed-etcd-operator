#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from platform import machine
from time import sleep

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION, TLSType
from statuses import CharmStatuses, TLSStatuses
from tests.integration.ha.helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    start_continuous_writes,
    stop_continuous_writes,
)
from tests.integration.ha.helpers_network import (
    cut_network_from_unit_with_ip_change,
    get_controller_hostname,
    hostname_from_unit,
    ip_address_from_unit,
    is_unit_reachable,
    restore_network_for_unit_with_ip_change,
)
from tests.integration.ha.upgrades.literals import (
    CERTIFICATE_EXPIRY_TIME,
    CHARM_CHANNEL,
    CHARM_REVISIONS_TO_DEPLOY,
    NUM_UNITS,
    WORKLOAD_VERSION,
)
from tests.integration.helpers import (
    APP_NAME,
    TLS_NAME,
    get_certificate_from_unit,
    get_cluster_endpoints,
    get_cluster_members,
    get_etcd_version,
    get_leader_unit_name,
    get_remaining_endpoints,
    get_secret_by_label,
    get_unit_endpoint,
)
from tests.integration.helpers_deployment import (
    ExpectedStatus,
    agents_idle,
    apps_active_and_agents_idle,
    does_status_match,
)

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
def test_disaster_recovery_during_upgrade(charm: str, juju_lxd_model: Juju) -> None:
    """Recover a failed cluster of two units during an upgrade."""
    juju_lxd_model.deploy(
        APP_NAME,
        num_units=2,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=2))

    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(app=APP_NAME, path=charm)

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(lambda status: agents_idle(status, APP_NAME, unit_count=2, idle_period=30))

    last_unit_name, last_unit_status = list(juju_lxd_model.status().get_units(APP_NAME).items())[
        -1
    ]
    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        juju_lxd_model.run(last_unit_name, "force-refresh-start", {"check-compatibility": False})

    juju_lxd_model.wait(lambda status: agents_idle(status, APP_NAME, unit_count=2, idle_period=30))

    leader_unit = get_leader_unit_name(juju_lxd_model, APP_NAME)

    logger.info("Rebuilding cluster to simulate recovery after majority failure")
    rebuild_response = juju_lxd_model.run(leader_unit, "rebuild-cluster", {"force": True})
    assert rebuild_response.return_code == 0, "rebuild failed"

    # TODO remove once we have a start upgrade tests with a charm revision with v1
    # data interfaces v1 uses - instead of _ for relation data keys
    # this breaks disaster recovery during upgrades if the upgraded unit is not the leader
    juju_lxd_model.wait(lambda status: agents_idle(status, APP_NAME, unit_count=2))

    # cluster should be recovered again
    assert "Cluster failure" not in last_unit_status.workload_status.message, (
        "Cluster could not be recovered"
    )
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        assert any(unit_name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit_name} is not in {cluster_members}"
        )
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    assert "resume-refresh" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message, (
        "Refresh should wait for user to continue with `resume-refresh` action"
    )
    logger.info("Continue refresh on all other units with `resume-refresh` action")
    resume_refresh_response = juju_lxd_model.run(
        list(juju_lxd_model.status().get_units(APP_NAME))[0], "resume-refresh"
    )
    assert resume_refresh_response.return_code == 0, "action failed"

    # wait for upgrade to complete
    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=2))
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd versions and cluster membership")

    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        unit_endpoint = get_unit_endpoint(juju_lxd_model, unit_name=unit_name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit_name} was not upgraded"

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    juju_lxd_model.remove_application(APP_NAME)
    juju_lxd_model.wait(lambda status: not juju_lxd_model.status().get_units(APP_NAME))


@pytest.mark.abort_on_fail
def test_ip_address_change_during_upgrade(charm: str, juju_lxd_model: Juju) -> None:
    """Process an updated ip address during an upgrade."""
    juju_lxd_model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(app=APP_NAME, path=charm)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        juju_lxd_model.status().get_units(APP_NAME),
        key=lambda unit_name: int(unit_name.split("/")[1]),
        reverse=True,
    )

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(
        lambda status: agents_idle(status, APP_NAME, unit_count=NUM_UNITS, idle_period=30)
    )

    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info(f"Continue refresh on unit {refresh_order[0]}")
        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        force_refresh_response = juju_lxd_model.run(
            refresh_order[0], "force-refresh-start", {"check-compatibility": False}
        )
        assert force_refresh_response.return_code == 0, "action failed"

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status, expected_status={APP_NAME: ExpectedStatus(app_status=["blocked"])}
        )
    )

    logger.info(f"Force new ip address for {refresh_order[-1]}")
    ip_renewal_hostname = hostname_from_unit(juju_lxd_model, unit_name=refresh_order[-1])
    old_unit_ip = ip_address_from_unit(juju_lxd_model, unit_name=refresh_order[-1])
    old_unit_endpoint = get_unit_endpoint(juju_lxd_model, unit_name=refresh_order[-1])
    cut_network_from_unit_with_ip_change(ip_renewal_hostname)

    # make sure the unit is not reachable from the controller
    controller_hostname = get_controller_hostname(juju_lxd_model)
    assert not is_unit_reachable(controller_hostname, ip_renewal_hostname), (
        f"unit {refresh_order[-1]} is still reachable from controller"
    )
    logger.info(f"{refresh_order[-1]} is not reachable via network.")

    # as the stopped member is unresponsive, only query the endpoints still available
    remaining_endpoints = get_remaining_endpoints(endpoints, old_unit_endpoint)
    assert_continuous_writes_increasing(
        endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
    )

    # reconnect the network for the disconnected unit
    restore_network_for_unit_with_ip_change(ip_renewal_hostname)
    logger.info(f"Network has been restored for {refresh_order[-1]}")

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(
                    app_status=["blocked"], unit_status=["active"], unit_count=NUM_UNITS
                )
            },
        )
    )

    # ensure the unit is up again
    new_unit_ip = ip_address_from_unit(juju_lxd_model, unit_name=refresh_order[-1])
    logger.info(f"{refresh_order[-1]} is available again with new ip {new_unit_ip}")

    logger.info("Continue refresh with `resume-refresh` action")
    resume_refresh_response = juju_lxd_model.run(refresh_order[1], "resume-refresh")
    assert resume_refresh_response.return_code == 0, "action failed"

    # wait for upgrade to complete
    juju_lxd_model.wait(
        lambda status: apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    endpoints_updated = endpoints.replace(old_unit_ip, new_unit_ip)
    assert_continuous_writes_increasing(
        endpoints=endpoints_updated, user=INTERNAL_USER, password=password
    )

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints_updated, user=INTERNAL_USER, password=password)
    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        unit_endpoint = get_unit_endpoint(juju_lxd_model, unit_name=unit_name, app_name=APP_NAME)
        # workaround in case ip address was not updated in `unit.public_address`
        unit_endpoint = unit_endpoint.replace(old_unit_ip, new_unit_ip)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit_name} was not upgraded"

        assert any(unit_name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit_name} is not in {cluster_members}"
        )

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    assert_continuous_writes_consistent(
        endpoints=endpoints_updated, user=INTERNAL_USER, password=password
    )
    juju_lxd_model.remove_application(APP_NAME)
    juju_lxd_model.wait(lambda status: not juju_lxd_model.status().get_units(APP_NAME))


@pytest.mark.abort_on_fail
def test_tls_cert_rotation_during_upgrade(charm: str, juju_lxd_model: Juju) -> None:
    """Process new TLS certificates during an upgrade."""
    juju_lxd_model.deploy(
        APP_NAME,
        num_units=NUM_UNITS,
        channel=CHARM_CHANNEL,
        revision=CHARM_REVISIONS_TO_DEPLOY[machine()],
    )

    # Deploy the TLS charm
    tls_config = {"ca-common-name": "etcd", "certificate-validity": "3m"}
    juju_lxd_model.deploy(TLS_NAME, channel="1/edge", config=tls_config)

    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME, TLS_NAME))

    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Integrating peer-certificates relation")
    juju_lxd_model.integrate(f"{APP_NAME}:peer-certificates", TLS_NAME)

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(unit_status=[TLSStatuses.TLS_PEER_CERTS_EXPIRING.value]),
                TLS_NAME: ExpectedStatus(unit_status=[CharmStatuses.ACTIVE_IDLE.value]),
            },
        )
    )

    # initiate the upgrade
    logger.info(f"Refresh etcd to v{WORKLOAD_VERSION['target']}")
    juju_lxd_model.refresh(app=APP_NAME, path=charm)

    # Refresh always happens from highest to lowest unit number
    refresh_order = sorted(
        juju_lxd_model.status().get_units(APP_NAME),
        key=lambda unit_name: int(unit_name.split("/")[1]),
        reverse=True,
    )

    logger.info(f"Getting current certificate from unit {refresh_order[-1]}")
    current_peer_certificate = get_certificate_from_unit(
        juju_lxd_model, refresh_order[-1], cert_type=TLSType.PEER
    )
    assert current_peer_certificate, "Failed to get current peer certificate"

    # versions will always be marked "incompatible" if refresh to a local version
    # this will not be the case when the PR is released
    # see: https://github.com/canonical/charm-refresh/blob/main/charm_refresh/_main.py#L182-L185
    juju_lxd_model.wait(lambda status: agents_idle(status, APP_NAME, idle_period=30))

    if "incompatible" in juju_lxd_model.status().apps.get(APP_NAME).app_status.message:
        logger.info("Upgrade is blocked due to incompatibility")

        logger.info("Running `force-refresh-start` action with check-compatibility=false")
        juju_lxd_model.run(refresh_order[0], "force-refresh-start", {"check-compatibility": False})

    juju_lxd_model.wait(
        lambda status: does_status_match(
            status, expected_status={APP_NAME: ExpectedStatus(app_status=["blocked"])}
        )
    )

    # wait for certificate to expire
    logger.info("Waiting for certificate to expire")
    sleep(CERTIFICATE_EXPIRY_TIME)

    new_peer_certificate = get_certificate_from_unit(
        juju_lxd_model, refresh_order[-1], cert_type=TLSType.PEER
    )
    assert new_peer_certificate, "Failed to get new peer certificate"
    assert new_peer_certificate != current_peer_certificate, (
        "Certificates are the same after rotation"
    )

    logger.info("Continue refresh with `resume-refresh` action")
    resume_refresh_response = juju_lxd_model.run(refresh_order[1], "resume-refresh")
    assert resume_refresh_response.return_code == 0, "action failed"

    # wait for upgrade to complete
    juju_lxd_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(
                    app_status=[TLSStatuses.TLS_PEER_CERTS_EXPIRING.value],
                    unit_status=[TLSStatuses.TLS_PEER_CERTS_EXPIRING.value],
                ),
                TLS_NAME: ExpectedStatus(
                    app_status=[CharmStatuses.ACTIVE_IDLE.value],
                    unit_status=[CharmStatuses.ACTIVE_IDLE.value],
                ),
            },
        )
    )
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)

    logger.info("Check etcd versions and cluster membership")
    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    for unit_name in juju_lxd_model.status().get_units(APP_NAME):
        unit_endpoint = get_unit_endpoint(juju_lxd_model, unit_name=unit_name, app_name=APP_NAME)
        assert (
            get_etcd_version(unit_endpoint, user=INTERNAL_USER, password=password)
            == WORKLOAD_VERSION["target"]
        ), f"unit {unit_name} was not upgraded"

        assert any(unit_name.replace("/", "") == member["name"] for member in cluster_members), (
            f"{unit_name} is not in {cluster_members}"
        )

    # clean up and remove the application to allow for further upgrade tests
    stop_continuous_writes()
    assert_continuous_writes_consistent(endpoints=endpoints, user=INTERNAL_USER, password=password)
