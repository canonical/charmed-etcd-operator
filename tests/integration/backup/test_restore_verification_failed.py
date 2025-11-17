#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, PEER_RELATION
from statuses import BackupStatuses

from ..helpers import (
    APP_NAME,
    add_secret,
    get_cluster_endpoints,
    get_cluster_members,
    get_key,
    get_secret_by_label,
    put_key,
    set_password,
)
from ..helpers_deployment import wait_until

logger = logging.getLogger(__name__)

NUM_UNITS = 3
S3_INTEGRATOR = "s3-integrator"
TEST_KEY = "test_key"
TEST_VALUE = "42"
backup_id = ""


@pytest.mark.abort_on_fail
async def test_deploy_and_configure(
    charm: str, ops_test: OpsTest, storage_credentials, storage_config
) -> None:
    """Deploy and configure the charm and s3-integrator."""
    await ops_test.model.deploy(charm, num_units=NUM_UNITS)
    await ops_test.model.deploy(S3_INTEGRATOR, channel="2/edge", num_units=1)
    await wait_until(ops_test, apps=[S3_INTEGRATOR], apps_statuses=["blocked"])

    logger.info(f"Configure {S3_INTEGRATOR}")
    secret_name = "s3-credentials"
    secret_id = await add_secret(ops_test, secret_name, storage_credentials)
    await ops_test.model.grant_secret(secret_name, S3_INTEGRATOR)
    await ops_test.model.applications[S3_INTEGRATOR].set_config({"credentials": secret_id})
    await ops_test.model.applications[S3_INTEGRATOR].set_config(storage_config)

    s3_unit = ops_test.model.applications[S3_INTEGRATOR].units[0]
    set_credentials_action = await s3_unit.run_action(
        "sync-s3-credentials",
        **storage_credentials,
    )
    await set_credentials_action.wait()
    await wait_until(ops_test, apps=[APP_NAME, S3_INTEGRATOR], apps_statuses=["active"])


@pytest.mark.abort_on_fail
async def test_s3_integration(ops_test: OpsTest, s3_bucket) -> None:
    """Integrate charm and s3-integrator."""
    await ops_test.model.integrate(APP_NAME, S3_INTEGRATOR)
    await wait_until(
        ops_test,
        apps=[APP_NAME, S3_INTEGRATOR],
        apps_statuses=["active"],
        units_statuses=["active"],
    )

    # bucket should be created when integrating both
    assert s3_bucket.meta.client.head_bucket(Bucket=s3_bucket.name)


@pytest.mark.abort_on_fail
async def test_create_backup(ops_test: OpsTest) -> None:
    """Create a backup and upload to s3-storage."""
    global backup_id

    # Before creating a backup, enter some data
    secret = await get_secret_by_label(ops_test, label=f"{PEER_RELATION}.{APP_NAME}.app")
    initial_password = secret.get(f"{INTERNAL_USER}-password")
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
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

    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit
    logger.info(f"Creating backup on unit {leader_unit.name}")

    # `create-backup` will upload the backup to storage
    create_action = await leader_unit.run_action("create-backup")
    create_backup_response = await create_action.wait()

    backup_id = create_backup_response.results.get("backup-id", "")
    assert backup_id, "No backup-id in response"

    # `list-backups` will look up the backups in storage
    list_action = await leader_unit.run_action("list-backups")
    list_backups_response = await list_action.wait()
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
async def test_restore_verification_failed(ops_test: OpsTest):
    """Restore a backup with invalid admin password."""
    logger.info("Configure admin credentials in etcd")
    invalid_password = "invalid_password"
    await set_password(ops_test, invalid_password)
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)

    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    # download the backup from storage and try to restore it
    logger.info(f"Restoring backup {backup_id}")
    restore_action = await leader_unit.run_action("restore", **{"backup-id": backup_id})
    restore_backup_response = await restore_action.wait()
    assert restore_backup_response.results.get("return-code") == 0, "restore action failed"

    # the restore will fail because the current password is not valid for the backup-file
    await wait_until(
        ops_test,
        apps=[APP_NAME],
        units_full_statuses={
            APP_NAME: [BackupStatuses.RESTORE_VERIFICATION_FAILED.value],
        },
    )

    # ensure test data was not restored
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    assert not (
        get_key(endpoints, user=INTERNAL_USER, password=invalid_password, key=TEST_KEY)
        == TEST_VALUE
    ), "Test data was restored even though restore should have failed"

    # ensure cluster is fully formed
    cluster_members = get_cluster_members(endpoints)
    assert len(cluster_members) == NUM_UNITS, (
        f"Expected {NUM_UNITS} cluster members, got {len(cluster_members)}."
    )
