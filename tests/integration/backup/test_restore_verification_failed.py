#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from jubilant import Juju

from literals import INTERNAL_USER, PEER_RELATION
from statuses import BackupStatuses

from ..helpers import (
    APP_NAME,
    get_cluster_endpoints,
    get_cluster_members,
    get_key,
    get_leader_unit_name,
    get_secret_by_label,
    put_key,
    set_password,
)
from ..helpers_deployment import ExpectedStatus, are_apps_active_and_agents_idle, does_status_match

logger = logging.getLogger(__name__)

NUM_UNITS = 3
S3_INTEGRATOR = "s3-integrator"
TEST_KEY = "test_key"
TEST_VALUE = "42"
backup_id = ""


@pytest.mark.abort_on_fail
def test_deploy_and_configure(
    charm: str, juju_lxd_model: Juju, storage_credentials, storage_config
) -> None:
    """Deploy and configure the charm and s3-integrator."""
    juju_lxd_model.deploy(charm, num_units=NUM_UNITS)
    juju_lxd_model.deploy(S3_INTEGRATOR, channel="2/edge", num_units=1)
    juju_lxd_model.wait(
        lambda status: does_status_match(
            status, expected_status={S3_INTEGRATOR: ExpectedStatus(app_status=["blocked"])}
        )
    )

    logger.info(f"Configure {S3_INTEGRATOR}")
    secret_name = "s3-credentials"
    secret_uri = juju_lxd_model.add_secret(secret_name, storage_credentials)
    juju_lxd_model.grant_secret(secret_uri, S3_INTEGRATOR)
    juju_lxd_model.config(S3_INTEGRATOR, values={"credentials": secret_uri})
    juju_lxd_model.config(S3_INTEGRATOR, storage_config)
    juju_lxd_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(app_status=["active"]),
                S3_INTEGRATOR: ExpectedStatus(app_status=["active"]),
            },
        )
    )


@pytest.mark.abort_on_fail
def test_s3_integration(juju_lxd_model: Juju, s3_bucket) -> None:
    """Integrate charm and s3-integrator."""
    juju_lxd_model.integrate(APP_NAME, S3_INTEGRATOR)
    juju_lxd_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(app_status=["active"], unit_status=["active"]),
                S3_INTEGRATOR: ExpectedStatus(app_status=["active"], unit_status=["active"]),
            },
        )
    )

    # bucket should be created when integrating both
    assert s3_bucket.meta.client.head_bucket(Bucket=s3_bucket.name)


@pytest.mark.abort_on_fail
def test_create_backup(juju_lxd_model: Juju) -> None:
    """Create a backup and upload to s3-storage."""
    global backup_id

    # Before creating a backup, enter some data
    secret = get_secret_by_label(juju_lxd_model, label=f"{PEER_RELATION}.{APP_NAME}.app")
    initial_password = secret.get(f"{INTERNAL_USER}-password")
    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    assert (
        put_key(
            endpoints,
            user=INTERNAL_USER,
            password=initial_password,
            key=TEST_KEY,
            value=TEST_VALUE,
        )
        == "OK"
    )
    assert (
        get_key(endpoints, user=INTERNAL_USER, password=initial_password, key=TEST_KEY)
        == TEST_VALUE
    )

    leader_unit = get_leader_unit_name(juju_lxd_model, APP_NAME)
    logger.info(f"Creating backup on unit {leader_unit}")

    # `create-backup` will upload the backup to storage
    create_backup_response = juju_lxd_model.run(leader_unit, "create-backup")

    backup_id = create_backup_response.results.get("backup-id", "")
    assert backup_id, "No backup-id in response"

    # `list-backups` will look up the backups in storage
    list_backups_response = juju_lxd_model.run(leader_unit, "list-backups")
    backups = list_backups_response.results.get("backups", "")
    # example: 'backup-id | backup-status\n--------------------\n2025-04-01T08:40:45Z  | finished'
    assert backups.split("\n")[2].startswith(backup_id), (
        "previously created backup not on top of backups-list"
    )

    # update test data to later check if it was restored
    assert (
        put_key(endpoints, user=INTERNAL_USER, password=initial_password, key=TEST_KEY, value="0")
        == "OK"
    )


@pytest.mark.abort_on_fail
def test_restore_verification_failed(juju_lxd_model: Juju):
    """Restore a backup with invalid admin password."""
    logger.info("Configure admin credentials in etcd")
    invalid_password = "invalid_password"
    set_password(juju_lxd_model, invalid_password)
    juju_lxd_model.wait(
        lambda status: are_apps_active_and_agents_idle(status, APP_NAME, unit_count=NUM_UNITS)
    )

    leader_unit = get_leader_unit_name(juju_lxd_model, APP_NAME)

    # download the backup from storage and try to restore it
    logger.info(f"Restoring backup {backup_id}")
    restore_backup_response = juju_lxd_model.run(
        leader_unit, "restore", params={"backup-id": backup_id}
    )
    assert restore_backup_response.return_code == 0, "restore action failed"

    # the restore will fail because the current password is not valid for the backup-file
    juju_lxd_model.wait(
        lambda status: does_status_match(
            status,
            expected_status={
                APP_NAME: ExpectedStatus(
                    unit_status=[BackupStatuses.RESTORE_VERIFICATION_FAILED.value]
                ),
            },
        )
    )

    # ensure test data was not restored
    endpoints = get_cluster_endpoints(juju_lxd_model, APP_NAME)
    assert not (
        get_key(endpoints, user=INTERNAL_USER, password=invalid_password, key=TEST_KEY)
        == TEST_VALUE
    ), "Test data was restored even though restore should have failed"

    # ensure cluster is fully formed
    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"Expected {NUM_UNITS} cluster members, got {len(cluster_members)}."
    )
