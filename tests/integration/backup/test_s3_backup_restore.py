#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from jubilant import Juju

from literals import INTERNAL_USER

from ..helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_key,
    get_leader_unit_name,
    put_key,
    set_password,
)
from ..helpers_deployment import ExpectedStatus, are_apps_active_and_agents_idle, does_status_match

logger = logging.getLogger(__name__)

NUM_UNITS = 3
S3_INTEGRATOR = "s3-integrator"
TEST_KEY = "test_key"
TEST_VALUE = "42"
PASSWORD = "some-password"
backup_id = ""


@pytest.mark.abort_on_fail
def test_deploy_and_configure(
    charm: str, juju_vm_model: Juju, storage_credentials, storage_config
) -> None:
    """Deploy and configure the charm and s3-integrator."""
    juju_vm_model.deploy(charm, num_units=NUM_UNITS)
    juju_vm_model.deploy(S3_INTEGRATOR, channel="2/edge", num_units=1)
    juju_vm_model.wait(
        lambda status: does_status_match(
            status, expected_status={S3_INTEGRATOR: ExpectedStatus(app_status=["blocked"])}
        )
    )

    logger.info(f"Configure {S3_INTEGRATOR}")
    secret_name = "s3-credentials"
    secret_uri = juju_vm_model.add_secret(secret_name, storage_credentials)
    juju_vm_model.grant_secret(secret_uri, S3_INTEGRATOR)
    juju_vm_model.config(S3_INTEGRATOR, {"credentials": secret_uri})
    juju_vm_model.config(S3_INTEGRATOR, storage_config)
    juju_vm_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(app_status=["active"]),
                S3_INTEGRATOR: ExpectedStatus(app_status=["active"]),
            },
        )
    )

    logger.info("Configure admin credentials in etcd")
    set_password(juju_vm_model, PASSWORD)
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )


@pytest.mark.abort_on_fail
def test_s3_integration(juju_vm_model: Juju, s3_bucket) -> None:
    """Integrate charm and s3-integrator."""
    juju_vm_model.integrate(APP_NAME, S3_INTEGRATOR)
    juju_vm_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(app_status=["active"], idle_period=30),
                S3_INTEGRATOR: ExpectedStatus(app_status=["active"], idle_period=30),
            },
        )
    )

    # bucket should be created when integrating both
    assert s3_bucket.meta.client.head_bucket(Bucket=s3_bucket.name)


@pytest.mark.abort_on_fail
def test_create_backup(juju_vm_model: Juju) -> None:
    """Create a backup and upload to s3-storage."""
    global backup_id

    # Before creating a backup, enter some data
    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME)
    assert (
        put_key(
            endpoints,
            user=INTERNAL_USER,
            password=PASSWORD,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    assert get_key(endpoints, user=INTERNAL_USER, password=PASSWORD, key=TEST_KEY) == TEST_VALUE

    leader_unit = get_leader_unit_name(juju_vm_model, APP_NAME)
    logger.info(f"Creating backup on unit {leader_unit}")

    # `create-backup` will upload the backup to storage
    create_backup_response = juju_vm_model.run(leader_unit, "create-backup")

    backup_id = create_backup_response.results.get("backup-id", "")
    assert backup_id, "No backup-id in response"

    # `list-backups` will look up the backups in storage
    list_backups_response = juju_vm_model.run(leader_unit, "list-backups")
    backups = list_backups_response.results.get("backups", "")
    # example: 'backup-id | backup-status\n--------------------\n2025-04-01T08:40:45Z  | finished'
    assert backups.split("\n")[2].startswith(backup_id), (
        "previously created backup not on top of backups-list"
    )

    # update test data to later check if it was restored
    assert (
        put_key(endpoints, user=INTERNAL_USER, password=PASSWORD, key=TEST_KEY, value="0") == "OK"
    )


@pytest.mark.abort_on_fail
def test_restore_backup_on_same_cluster(juju_vm_model: Juju) -> None:
    """Restore a backup and check if data is recovered."""
    leader_unit = get_leader_unit_name(juju_vm_model, APP_NAME)

    # download the backup from storage and restore it
    logger.info(f"Restoring backup {backup_id}")
    restore_backup_response = juju_vm_model.run(leader_unit, "restore", {"backup-id": backup_id})
    assert restore_backup_response.return_code == 0, "restore failed"

    # wait for the restore to be performed across all units and check the restored data
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME)
    assert get_key(endpoints, user=INTERNAL_USER, password=PASSWORD, key=TEST_KEY) == TEST_VALUE, (
        "data not recovered"
    )


@pytest.mark.abort_on_fail
def test_restore_backup_on_different_cluster(charm: str, juju_vm_model: Juju):
    """Restore a backup and check if data is recovered."""
    logger.info("Remove existing etcd cluster and deploy a new one.")
    juju_vm_model.remove_application(APP_NAME)
    juju_vm_model.remove_secret(identifier="system_users_secret")
    juju_vm_model.wait(lambda status: status.apps.get(APP_NAME) is None)

    juju_vm_model.deploy(charm, num_units=NUM_UNITS)
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(
            status, APP_NAME, unit_count=NUM_UNITS, idle_period=60
        )
    )

    logger.info("Configure admin credentials in etcd")
    set_password(juju_vm_model, PASSWORD)
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    logger.info(f"Integrate the newly deployed application with {S3_INTEGRATOR}")
    juju_vm_model.integrate(APP_NAME, S3_INTEGRATOR)
    juju_vm_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(app_status=["active"], unit_status=["active"]),
                S3_INTEGRATOR: ExpectedStatus(app_status=["active"], unit_status=["active"]),
            },
        )
    )

    leader_unit = get_leader_unit_name(juju_vm_model, APP_NAME)

    # download the backup from storage and restore it
    logger.info(f"Restoring backup {backup_id}")
    restore_backup_response = juju_vm_model.run(leader_unit, "restore", {"backup-id": backup_id})
    assert restore_backup_response.return_code == 0, "restore failed"

    # wait for the restore to be performed across all units and check the restored data
    juju_vm_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    endpoints = get_cluster_endpoints(juju_vm_model, APP_NAME)
    assert get_key(endpoints, user=INTERNAL_USER, password=PASSWORD, key=TEST_KEY) == TEST_VALUE, (
        "data not recovered"
    )
