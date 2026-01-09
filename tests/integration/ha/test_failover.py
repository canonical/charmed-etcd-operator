#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import time

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION

from ..helpers import (
    APP_NAME,
    get_cluster_endpoints_jubilant,
    get_cluster_members,
    get_raft_leader,
    get_remaining_endpoints,
    get_secret_by_label_jubilant,
    get_unit_endpoint_jubilant,
    is_endpoint_up,
)
from ..helpers_deployment import apps_active_and_agents_idle, does_status_match
from .helpers import (
    assert_continuous_writes_consistent,
    assert_continuous_writes_increasing,
    existing_app_jubilant,
    send_process_control_signal,
    start_continuous_writes,
    stop_continuous_writes,
)

logger = logging.getLogger(__name__)

NUM_UNITS = 3
RESTART_DELAY_DEFAULT = 20
RESTART_DELAY_PATCHED = 120
TEST_KEY = "test_key"
TEST_VALUE = "42"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(charm: str, juju_lxd_model: Juju) -> None:
    """Build and deploy the charm, allowing for skipping if already deployed."""
    # it is possible for users to provide their own cluster for HA testing.
    if existing_app_jubilant(juju_lxd_model):
        return

    # Deploy the charm and wait for active/idle status
    juju_lxd_model.deploy(charm, num_units=NUM_UNITS)
    juju_lxd_model.wait(lambda status: apps_active_and_agents_idle(status, APP_NAME), timeout=1000)


@pytest.mark.abort_on_fail
async def test_kill_db_process_on_raft_leader(etcd_process: str, juju_lxd_model: Juju) -> None:
    """Make sure the cluster can self-heal when the leader goes down."""
    app = (existing_app_jubilant(juju_lxd_model)) or APP_NAME

    # make sure we have at least two units so we can stop one of them
    if len(juju_lxd_model.status().get_units(app)) < 2:
        juju_lxd_model.add_unit(app)
        juju_lxd_model.wait(
            lambda status: does_status_match(
                status,
                expected_app_statuses={app: ["active"]},
                expected_unit_statuses={app: ["active"]},
                num_units={app: 2},
            )
        )

    init_units_count = len(juju_lxd_model.status().get_units(app))
    endpoints = get_cluster_endpoints_jubilant(juju_lxd_model, app)
    secret = get_secret_by_label_jubilant(juju_lxd_model, label=f"{PEER_RELATION}.{app}.app")
    password = secret.get(f"{INTERNAL_USER}-password")

    # start writing data to the cluster
    start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
    time.sleep(10)

    # get details for the current raft leader in the cluster
    initial_raft_leader = get_raft_leader(
        endpoints=endpoints, user=INTERNAL_USER, password=password
    )
    logger.info(f"initial raft leader: {initial_raft_leader}")
    leader_unit = initial_raft_leader.replace(app, f"{app}/")

    # axe away the etcd process of the cluster/raft leader
    send_process_control_signal(
        unit_name=leader_unit,
        model_full_name=juju_lxd_model.model,
        signal="SIGKILL",
        etcd_process=etcd_process,
    )

    # make sure the process is stopped
    unit_endpoint = get_unit_endpoint_jubilant(juju_lxd_model, unit_name=leader_unit, app_name=app)
    assert not is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
    logger.info(f"{leader_unit} is stopped.")

    # as the stopped member is unresponsive, only query the endpoints still available
    remaining_endpoints = get_remaining_endpoints(endpoints, unit_endpoint)

    # ensure a new leader was assigned after waiting for the `election timeout`
    time.sleep(3)
    new_raft_leader = get_raft_leader(
        endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
    )
    logger.info(f"new raft leader: {new_raft_leader}")
    assert new_raft_leader != initial_raft_leader, (
        "raft leadership not transferred after stop of leader"
    )

    # ensure data is continuing to be written in the cluster
    assert_continuous_writes_increasing(
        endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
    )

    # ensure the stopped unit was restarted
    time.sleep(RESTART_DELAY_DEFAULT)
    assert is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
    logger.info(f"{leader_unit} is available again.")

    cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
    assert len(cluster_members) == init_units_count, (
        f"expected {init_units_count} cluster members, got {len(cluster_members)}"
    )
    logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")

    # ensure data is written in the cluster
    assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
    stop_continuous_writes()
    # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
    # that's why we will miss writes here, and therefore ignore the revision of the key
    assert_continuous_writes_consistent(
        endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
    )


# @pytest.mark.abort_on_fail
# async def test_freeze_db_process_on_raft_leader(etcd_process: str, juju_lxd_model: Juju) -> None:
#     """Make sure the cluster can self-heal when the leader stops."""
#     app = existing_app_jubilant(juju_lxd_model) or APP_NAME
#
#     # make sure we have at least two units so we can stop one of them
#     if len(ops_test.model.applications[app].units) < 2:
#         await ops_test.model.applications[app].add_unit(count=1)
#         await wait_until(
#             ops_test,
#             apps=[app],
#             apps_statuses=["active"],
#             units_statuses=["active"],
#             wait_for_exact_units=2,
#         )
#
#     init_units_count = len(ops_test.model.applications[app].units)
#     endpoints = get_cluster_endpoints(ops_test, app)
#     secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
#     password = secret.get(f"{INTERNAL_USER}-password")
#
#     # start writing data to the cluster
#     start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     time.sleep(10)
#
#     # get details for the current raft leader in the cluster
#     initial_raft_leader = get_raft_leader(
#         endpoints=endpoints, user=INTERNAL_USER, password=password
#     )
#     logger.info(f"initial raft leader: {initial_raft_leader}")
#     leader_unit = initial_raft_leader.replace(app, f"{app}/")
#
#     # freeze the etcd process of the cluster/raft leader
#     send_process_control_signal(
#         unit_name=leader_unit,
#         model_full_name=ops_test.model_full_name,
#         signal="SIGSTOP",
#         etcd_process=etcd_process,
#     )
#
#     # wait until the SIGSTOP fully takes effect
#     time.sleep(10)
#
#     # ensure the stopped unit is not reachable
#     unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
#     assert not is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#     logger.info(f"{leader_unit} is stopped.")
#
#     # make sure leadership was moved
#     # as the stopped member is unresponsive, only query the endpoints still available
#     remaining_endpoints = get_remaining_endpoints(endpoints, unit_endpoint)
#     new_raft_leader = get_raft_leader(
#         endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
#     )
#     logger.info(f"new raft leader: {new_raft_leader}")
#     assert new_raft_leader != initial_raft_leader, (
#         "raft leadership not transferred after freeze of leader"
#     )
#
#     # ensure data is still written in the cluster
#     assert_continuous_writes_increasing(
#         endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
#     )
#
#     # continue the etcd process
#     send_process_control_signal(
#         unit_name=leader_unit,
#         model_full_name=ops_test.model_full_name,
#         signal="SIGCONT",
#         etcd_process=etcd_process,
#     )
#
#     # ensure the stopped unit is reachable again
#     assert is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#     logger.info(f"{leader_unit} is available again.")
#
#     cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
#     assert len(cluster_members) == init_units_count, (
#         f"expected {init_units_count} cluster members, got {len(cluster_members)}"
#     )
#     logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")
#
#     stop_continuous_writes()
#     # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
#     # that's why we will miss writes here, and therefore ignore the revision of the key
#     assert_continuous_writes_consistent(
#         endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
#     )
#
#
# @pytest.mark.abort_on_fail
# async def test_restart_db_process_on_raft_leader(etcd_process: str, juju_lxd_model: Juju) -> None:
#     """Make sure the cluster can self-heal when the leader goes down."""
#     app = (await existing_app(ops_test)) or APP_NAME
#
#     # make sure we have at least two units so we can kill one of them
#     if len(ops_test.model.applications[app].units) < 2:
#         await ops_test.model.applications[app].add_unit(count=1)
#         await wait_until(
#             ops_test,
#             apps=[app],
#             apps_statuses=["active"],
#             units_statuses=["active"],
#             wait_for_exact_units=2,
#         )
#
#     init_units_count = len(ops_test.model.applications[app].units)
#     endpoints = get_cluster_endpoints(ops_test, app)
#     secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
#     password = secret.get(f"{INTERNAL_USER}-password")
#
#     # start writing data to the cluster
#     start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     time.sleep(10)
#
#     # get details for the current raft leader in the cluster
#     initial_raft_leader = get_raft_leader(
#         endpoints=endpoints, user=INTERNAL_USER, password=password
#     )
#     logger.info(f"initial raft leader: {initial_raft_leader}")
#     leader_unit = initial_raft_leader.replace(app, f"{app}/")
#
#     # axe away the etcd process of the cluster/raft leader
#     send_process_control_signal(
#         unit_name=leader_unit,
#         model_full_name=ops_test.model_full_name,
#         signal="SIGTERM",
#         etcd_process=etcd_process,
#     )
#
#     # make sure the process is stopped
#     unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
#     assert not is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#
#     # as the stopped member is unresponsive, only query the endpoints still available
#     remaining_endpoints = get_remaining_endpoints(endpoints, unit_endpoint)
#
#     # ensure a new leader was assigned after waiting for the `election timeout`
#     time.sleep(3)
#     new_raft_leader = get_raft_leader(
#         endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
#     )
#     logger.info(f"new raft leader: {new_raft_leader}")
#     assert new_raft_leader != initial_raft_leader, (
#         "raft leadership not transferred after freeze of leader"
#     )
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(
#         endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
#     )
#
#     # ensure the stopped unit was restarted
#     time.sleep(RESTART_DELAY_DEFAULT)
#     assert is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#     logger.info(f"{leader_unit} is available again.")
#
#     cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
#     assert len(cluster_members) == init_units_count, (
#         f"expected {init_units_count} cluster members, got {len(cluster_members)}"
#     )
#     logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     stop_continuous_writes()
#     # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
#     # that's why we will miss writes here, and therefore ignore the revision of the key
#     assert_continuous_writes_consistent(
#         endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
#     )
#
#
# @pytest.mark.abort_on_fail
# async def test_full_cluster_restart(etcd_process: str, ops_test: OpsTest) -> None:
#     """Make sure the cluster can self-heal after all members went down."""
#     app = (await existing_app(ops_test)) or APP_NAME
#
#     # make sure we have at least two units so we can kill one of them
#     if len(ops_test.model.applications[app].units) < 2:
#         await ops_test.model.applications[app].add_unit(count=1)
#         await wait_until(
#             ops_test,
#             apps=[app],
#             apps_statuses=["active"],
#             units_statuses=["active"],
#             wait_for_exact_units=2,
#         )
#
#     init_units_count = len(ops_test.model.applications[app].units)
#     endpoints = get_cluster_endpoints(ops_test, app)
#     secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
#     password = secret.get(f"{INTERNAL_USER}-password")
#
#     # start writing data to the cluster
#     start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     time.sleep(10)
#
#     # update the restart delay for all units
#     for unit in ops_test.model.applications[app].units:
#         await patch_restart_delay(ops_test, unit_name=unit.name, delay=RESTART_DELAY_PATCHED)
#
#     # axe away the etcd process on all units
#     for unit in ops_test.model.applications[app].units:
#         send_process_control_signal(
#             unit_name=unit.name,
#             model_full_name=ops_test.model_full_name,
#             signal="SIGTERM",
#             etcd_process=etcd_process,
#         )
#
#     # ensure the all cluster members are down
#     for unit in ops_test.model.applications[app].units:
#         unit_endpoint = get_unit_endpoint(ops_test, unit_name=unit.name, app_name=app)
#         assert not is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#     logger.info("Cluster is not available after being stopped.")
#
#     logger.info(f"Waiting {RESTART_DELAY_PATCHED}s for service restarts.")
#     time.sleep(RESTART_DELAY_PATCHED)
#
#     # now check the availability and formation of the cluster
#     cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
#     assert len(cluster_members) == init_units_count, (
#         f"expected {init_units_count} cluster members, got {len(cluster_members)}"
#     )
#     logger.info(f"Cluster has come back, all {len(cluster_members)} members joined the cluster.")
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     stop_continuous_writes()
#     assert_continuous_writes_consistent(
#         endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
#     )
#
#     # reset the restart delay to the original value
#     for unit in ops_test.model.applications[app].units:
#         await patch_restart_delay(ops_test, unit_name=unit.name, delay=RESTART_DELAY_DEFAULT)
#
#
# @pytest.mark.abort_on_fail
# async def test_full_cluster_crash(etcd_process: str, juju_lxd_model: Juju) -> None:
#     """Make sure the cluster can self-heal after all members went down."""
#     app = (await existing_app(ops_test)) or APP_NAME
#
#     # make sure we have at least two units so we can kill one of them
#     if len(ops_test.model.applications[app].units) < 2:
#         await ops_test.model.applications[app].add_unit(count=1)
#         await wait_until(
#             ops_test,
#             apps=[app],
#             apps_statuses=["active"],
#             units_statuses=["active"],
#             wait_for_exact_units=2,
#         )
#
#     init_units_count = len(ops_test.model.applications[app].units)
#     endpoints = get_cluster_endpoints(ops_test, app)
#     secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
#     password = secret.get(f"{INTERNAL_USER}-password")
#
#     # start writing data to the cluster
#     start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     time.sleep(10)
#
#     # update the restart delay for all units
#     for unit in ops_test.model.applications[app].units:
#         await patch_restart_delay(ops_test, unit_name=unit.name, delay=RESTART_DELAY_PATCHED)
#
#     # axe away the etcd process on all units
#     for unit in ops_test.model.applications[app].units:
#         send_process_control_signal(
#             unit_name=unit.name,
#             model_full_name=ops_test.model_full_name,
#             signal="SIGKILL",
#             etcd_process=etcd_process,
#         )
#
#     # ensure the all cluster members are down
#     for unit in ops_test.model.applications[app].units:
#         unit_endpoint = get_unit_endpoint(ops_test, unit_name=unit.name, app_name=app)
#         assert not is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#     logger.info("Cluster is not available after crash.")
#
#     logger.info(f"Waiting {RESTART_DELAY_PATCHED}s for service restarts.")
#     time.sleep(RESTART_DELAY_PATCHED)
#
#     # now check the availability and formation of the cluster
#     cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
#     assert len(cluster_members) == init_units_count, (
#         f"expected {init_units_count} cluster members, got {len(cluster_members)}"
#     )
#     logger.info(f"Cluster has come back, all {len(cluster_members)} joined the cluster.")
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     stop_continuous_writes()
#     assert_continuous_writes_consistent(
#         endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
#     )
#
#     # reset the restart delay to the original value
#     for unit in ops_test.model.applications[app].units:
#         await patch_restart_delay(ops_test, unit_name=unit.name, delay=RESTART_DELAY_DEFAULT)
#
#
# @pytest.mark.abort_on_fail
# async def test_restart_raft_leader_after_deleting_database_file(
#     etcd_process: str, juju_lxd_model: Juju
# ) -> None:
#     """Make sure the cluster can self-heal when the leader's data is deleted."""
#     app = (await existing_app(ops_test)) or APP_NAME
#
#     # make sure we have at least two units so we can kill one of them
#     if len(ops_test.model.applications[app].units) < 2:
#         await ops_test.model.applications[app].add_unit(count=1)
#         await wait_until(
#             ops_test,
#             apps=[app],
#             apps_statuses=["active"],
#             units_statuses=["active"],
#             wait_for_exact_units=2,
#         )
#
#     init_units_count = len(ops_test.model.applications[app].units)
#     endpoints = get_cluster_endpoints(ops_test, app)
#     secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
#     password = secret.get(f"{INTERNAL_USER}-password")
#
#     # start writing data to the cluster
#     start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     time.sleep(10)
#
#     # get details for the current raft leader in the cluster
#     initial_raft_leader = get_raft_leader(
#         endpoints=endpoints, user=INTERNAL_USER, password=password
#     )
#     logger.info(f"initial raft leader: {initial_raft_leader}")
#     leader_unit = initial_raft_leader.replace(app, f"{app}/")
#
#     # stop the etcd process of the cluster/raft leader
#     await patch_restart_delay(ops_test, unit_name=leader_unit, delay=RESTART_DELAY_PATCHED)
#     send_process_control_signal(
#         unit_name=leader_unit,
#         model_full_name=ops_test.model_full_name,
#         signal="SIGTERM",
#         etcd_process=etcd_process,
#     )
#
#     # make sure the process is stopped
#     unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
#     assert not is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#
#     # forcefully remove database file on the unit
#     await remove_database_file(ops_test, unit_name=leader_unit)
#
#     # as the stopped member is unresponsive, only query the endpoints still available
#     remaining_endpoints = get_remaining_endpoints(endpoints, unit_endpoint)
#
#     # ensure a new leader was assigned after waiting for the `election timeout`
#     time.sleep(3)
#     new_raft_leader = get_raft_leader(
#         endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
#     )
#     logger.info(f"new raft leader: {new_raft_leader}")
#     assert new_raft_leader != initial_raft_leader, (
#         "raft leadership not transferred after freeze of leader"
#     )
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(
#         endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
#     )
#
#     # ensure the stopped unit was restarted
#     time.sleep(RESTART_DELAY_DEFAULT)
#     assert is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#     logger.info(f"{leader_unit} is available again.")
#
#     cluster_members = get_cluster_members(endpoints, user=INTERNAL_USER, password=password)
#     assert len(cluster_members) == init_units_count, (
#         f"expected {init_units_count} cluster members, got {len(cluster_members)}"
#     )
#     logger.info(f"Cluster fully formed again with {len(cluster_members)} members.")
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     stop_continuous_writes()
#     # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
#     # that's why we will miss writes here, and therefore ignore the revision of the key
#     assert_continuous_writes_consistent(
#         endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
#     )
#
#
# @pytest.mark.abort_on_fail
# async def test_reboot_raft_leader(etcd_process: str, juju_lxd_model: Juju) -> None:
#     """Make sure a unit comes back cleanly after rebooting the VM."""
#     app = (await existing_app(ops_test)) or APP_NAME
#
#     # make sure we have at least two units so we can kill one of them
#     if len(ops_test.model.applications[app].units) < 2:
#         await ops_test.model.applications[app].add_unit(count=1)
#         await wait_until(
#             ops_test,
#             apps=[app],
#             apps_statuses=["active"],
#             units_statuses=["active"],
#             wait_for_exact_units=2,
#         )
#
#     endpoints = get_cluster_endpoints(ops_test, app)
#     secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{app}.app")
#     password = secret.get(f"{INTERNAL_USER}-password")
#
#     # start writing data to the cluster
#     start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     time.sleep(10)
#
#     # get details for the current raft leader in the cluster
#     initial_raft_leader = get_raft_leader(
#         endpoints=endpoints, user=INTERNAL_USER, password=password
#     )
#     logger.info(f"initial raft leader: {initial_raft_leader}")
#     leader_unit = initial_raft_leader.replace(app, f"{app}/")
#
#     # forcefully reboot the unit
#     await reboot_unit(ops_test, unit_name=leader_unit)
#
#     # ensure a new leader was assigned after waiting for the `election timeout`
#     time.sleep(3)
#     unit_endpoint = get_unit_endpoint(ops_test, unit_name=leader_unit, app_name=app)
#     remaining_endpoints = get_remaining_endpoints(endpoints, unit_endpoint)
#     new_raft_leader = get_raft_leader(
#         endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
#     )
#     logger.info(f"new raft leader: {new_raft_leader}")
#     assert new_raft_leader != initial_raft_leader, (
#         "raft leadership not transferred after freeze of leader"
#     )
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(
#         endpoints=remaining_endpoints, user=INTERNAL_USER, password=password
#     )
#
#     # give some time for the reboot to complete
#     time.sleep(30)
#
#     # ensure the stopped unit was restarted
#     assert is_endpoint_up(unit_endpoint, user=INTERNAL_USER, password=password)
#     logger.info(f"{leader_unit} is available again.")
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     stop_continuous_writes()
#     # By default, etcd uses a 1s election timeout before attempting to replace a lost leader
#     # that's why we will miss writes here, and therefore ignore the revision of the key
#     assert_continuous_writes_consistent(
#         endpoints=endpoints, user=INTERNAL_USER, password=password, ignore_revision=True
#     )
#
#     # continue writing data
#     start_continuous_writes(endpoints=endpoints, user=INTERNAL_USER, password=password)
#     time.sleep(10)
#
#     # now reboot all units
#     units_count = len(ops_test.model.applications[app].units)
#     for unit in ops_test.model.applications[app].units:
#         await reboot_unit(ops_test, unit_name=unit.name)
#
#     await wait_until(
#         ops_test,
#         apps=[app],
#         apps_statuses=["active"],
#         units_statuses=["active"],
#         wait_for_exact_units=units_count,
#     )
#
#     # ensure data is written in the cluster
#     assert_continuous_writes_increasing(endpoints=endpoints, user=INTERNAL_USER, password=password)
