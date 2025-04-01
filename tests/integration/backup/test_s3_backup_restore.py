#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import INTERNAL_USER, INTERNAL_USER_PASSWORD_CONFIG

from ..helpers import (
    APP_NAME,
    CHARM_PATH,
    get_cluster_endpoints,
    get_key,
    put_key,
)
from ..helpers_deployment import wait_until

logger = logging.getLogger(__name__)

NUM_UNITS = 3
S3_INTEGRATOR = "s3-integrator"
TEST_KEY = "test_key"
TEST_VALUE = "42"
PASSWORD = "some-password"
backup_id = ""


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, storage_credentials, storage_config) -> None:
    """Deploy and configure the charm and s3-integrator."""
    await ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS)
    await ops_test.model.deploy(S3_INTEGRATOR, channel="latest/stable", num_units=1)
    await wait_until(ops_test, apps=[S3_INTEGRATOR], apps_statuses=["blocked"])

    logger.info(f"Configure {S3_INTEGRATOR}")
    await ops_test.model.applications[S3_INTEGRATOR].set_config(storage_config)

    s3_unit = ops_test.model.applications[S3_INTEGRATOR].units[0]
    provide_credentials_action = await s3_unit.run_action(
        "sync-s3-credentials",
        **storage_credentials,
    )
    await provide_credentials_action.wait()
    await wait_until(ops_test, apps=[APP_NAME, S3_INTEGRATOR], apps_statuses=["active"])

    logger.info("Configure admin credentials in etcd")
    secret_name = "test_secret"

    secret_id = await ops_test.model.add_secret(
        name=secret_name, data_args=[f"{INTERNAL_USER}={PASSWORD}"]
    )
    await ops_test.model.grant_secret(secret_name=secret_name, application=APP_NAME)

    # update the application config to include the secret
    await ops_test.model.applications[APP_NAME].set_config(
        {INTERNAL_USER_PASSWORD_CONFIG: secret_id}
    )
    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["active"])


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_s3_integration(ops_test: OpsTest, s3_bucket):
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


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_create_backup(ops_test: OpsTest):
    """Create a backup and upload to s3-storage."""
    # Before creating a backup, enter some data
    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
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

    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    # `create-backup` will upload the backup to storage
    create_action = await leader_unit.run_action("create-backup")
    create_backup_response = await create_action.wait()
    global backup_id
    backup_id = create_backup_response.results.get("backup-id", "")
    assert backup_id, "No backup-id in response"

    # `list-backups` will look up the backups in storage
    list_action = await leader_unit.run_action("list-backups")
    list_backups_response = await list_action.wait()
    backups = list_backups_response.results.get("backups", "")
    # example: 'backup-id | backup-status\n--------------------\n2025-04-01T08:40:45Z  | finished'
    assert backups.split("\n")[2].startswith(backup_id), (
        "previously created backup not in backups-list"
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_restore_backup_on_same_cluster(ops_test: OpsTest):
    """Restore a backup and check if data is recovered."""
    global backup_id

    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    # download the backup from storage and restore it
    logger.info(f"Restoring backup {backup_id}")
    restore_action = await leader_unit.run_action("restore", params={"backup-id": backup_id})
    restore_backup_response = await restore_action.wait()
    assert restore_backup_response.results.get("return-code") == 0, "restore failed"

    await wait_until(
        ops_test,
        apps=[APP_NAME],
        apps_statuses=["active"],
        units_statuses=["active"],
    )

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    assert get_key(endpoints, user=INTERNAL_USER, password=PASSWORD, key=TEST_KEY) == TEST_VALUE, (
        "data not recovered"
    )


@pytest.mark.runner(["self-hosted", "linux", "X64", "jammy"])
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_restore_backup_on_different_cluster(ops_test: OpsTest):
    """Restore a backup and check if data is recovered."""
    global backup_id

    logger.info("Remove existing etcd cluster and deploy a new one.")
    await ops_test.model.remove_application(APP_NAME, block_until_done=True)
    await ops_test.model.deploy(CHARM_PATH, num_units=NUM_UNITS)
    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS, idle_period=60)

    logger.info("Configure admin credentials in etcd")
    secret_name = "new_test_secret"

    secret_id = await ops_test.model.add_secret(
        name=secret_name, data_args=[f"{INTERNAL_USER}={PASSWORD}"]
    )
    await ops_test.model.grant_secret(secret_name=secret_name, application=APP_NAME)

    # update the application config to include the secret
    await ops_test.model.applications[APP_NAME].set_config(
        {INTERNAL_USER_PASSWORD_CONFIG: secret_id}
    )
    await wait_until(ops_test, apps=[APP_NAME], apps_statuses=["active"])

    logger.info(f"Integrate with {S3_INTEGRATOR}")
    await ops_test.model.integrate(APP_NAME, S3_INTEGRATOR)
    await wait_until(
        ops_test,
        apps=[APP_NAME, S3_INTEGRATOR],
        apps_statuses=["active"],
        units_statuses=["active"],
    )

    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    # download the backup from storage and restore it
    logger.info(f"Restoring backup {backup_id}")
    restore_action = await leader_unit.run_action("restore", params={"backup-id": backup_id})
    restore_backup_response = await restore_action.wait()
    assert restore_backup_response.results.get("return-code") == 0, "restore failed"

    await wait_until(ops_test, apps=[APP_NAME], wait_for_exact_units=NUM_UNITS)

    endpoints = get_cluster_endpoints(ops_test, APP_NAME)
    assert get_key(endpoints, user=INTERNAL_USER, password=PASSWORD, key=TEST_KEY) == TEST_VALUE, (
        "data not recovered"
    )
