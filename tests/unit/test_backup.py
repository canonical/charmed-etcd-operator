#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import patch

import ops
import yaml
from ops import testing
from pytest import raises
from scenario import Secret

from charm import EtcdOperatorCharm
from literals import (
    AZURE_RELATION_NAME,
    INTERNAL_USER_PASSWORD_CONFIG,
    PEER_RELATION,
    S3_RELATION_NAME,
    EtcdClusterState,
    RestoreStep,
)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def test_s3_relation():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    s3_relation = testing.Relation(
        id=2,
        interface="s3",
        endpoint=S3_RELATION_NAME,
        remote_app_name="s3",
        remote_app_data={
            "access-key": "mykey",
            "secret-key": "mysecret",
            "bucket": "mybucket",
            "endpoint": "myendpoint",
            "path": "mypath",
        },
    )
    with patch("managers.backup.BackupManager.create_bucket"):
        state_in = testing.State(relations={peer_relation, s3_relation}, leader=True)
        state_out = ctx.run(ctx.on.relation_changed(s3_relation), state_in)
        secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
        assert secret_out.latest_content.get("s3-credentials")

    # unhappy path - bucket name is missing in s3-integrator relation
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    s3_relation = testing.Relation(
        id=2,
        interface="s3",
        endpoint=S3_RELATION_NAME,
        remote_app_name="s3",
        remote_app_data={"access-key": "mykey", "secret-key": "mysecret"},
    )

    state_in = testing.State(relations={peer_relation, s3_relation}, leader=True)
    with patch("managers.backup.BackupManager.create_bucket"):
        with raises(testing.errors.UncaughtCharmError) as e:
            ctx.run(ctx.on.relation_changed(s3_relation), state_in)

        assert isinstance(e.value.__cause__, KeyError)


def test_azure_relation():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    azure_relation = testing.Relation(
        id=2,
        interface="azure",
        endpoint=AZURE_RELATION_NAME,
        remote_app_name="azure",
        remote_app_data={
            "connection-protocol": "abfss",
            "secret-key": "mysecret",
            "container": "mycontainer",
            "storage-account": "myaccount",
            "path": "mypath",
        },
    )
    with patch("managers.backup.BackupManager.create_container"):
        state_in = testing.State(relations={peer_relation, azure_relation}, leader=True)
        state_out = ctx.run(ctx.on.relation_changed(azure_relation), state_in)
        secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
        assert secret_out.latest_content.get("azure-credentials")

    # unhappy path - path is missing in azure-integrator relation
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    azure_relation = testing.Relation(
        id=2,
        interface="azure",
        endpoint=AZURE_RELATION_NAME,
        remote_app_name="azure",
        remote_app_data={
            "connection-protocol": "abfss",
            "secret-key": "mysecret",
            "container": "mycontainer",
            "storage-account": "myaccount",
        },
    )

    state_in = testing.State(relations={peer_relation, azure_relation}, leader=True)
    with patch("managers.backup.BackupManager.create_container"):
        with raises(testing.errors.UncaughtCharmError) as e:
            ctx.run(ctx.on.relation_changed(azure_relation), state_in)

        assert isinstance(e.value.__cause__, KeyError)


def test_create_backup_action_s3():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    s3_credentials = {
        "access-key": "mykey",
        "secret-key": "mysecret",
        "bucket": "mybucket",
        "endpoint": "myendpoint",
        "path": "mypath",
    }
    s3_relation = testing.Relation(
        id=2,
        interface="s3",
        endpoint=S3_RELATION_NAME,
        remote_app_name="s3",
        remote_app_data=s3_credentials,
    )

    # ensure backup cannot be created if run on non-leader unit
    state_in = testing.State(relations={peer_relation, s3_relation}, leader=False)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("create-backup"), state_in)

        assert e.message == "Action must be performed on the leader unit."

    # ensure backup cannot be created if no s3-credentials
    state_in = testing.State(relations={peer_relation, s3_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("create-backup"), state_in)

        assert e.message == "No credentials for object storage available."

    # ensure action fails if snapshot in etcd cannot be created
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(secrets=[secret], relations={peer_relation, s3_relation}, leader=True)
    with (
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="snapshot save")),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("create-backup"), state_in)

            assert e.message == "Failed to create database backup."

    # ensure action fails if backup already in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started"},
        local_app_data={"backup_id": "xyz"},
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(secrets=[secret], relations={peer_relation, s3_relation}, leader=True)
    with patch("subprocess.run"):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("create-backup"), state_in)

            assert e.message == "There is currently a backup in progress, please wait."

    # happy path
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(secrets=[secret], relations={peer_relation, s3_relation}, leader=True)
    with patch("managers.backup.BackupManager.create_backup", return_value="my_backup_id"):
        ctx.run(ctx.on.action("create-backup"), state_in)

    assert ctx.action_results == {"backup-id": "my_backup_id"}


def test_create_backup_action_azure():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    azure_relation = testing.Relation(
        id=2,
        interface="azure",
        endpoint=AZURE_RELATION_NAME,
        remote_app_name="azure",
        remote_app_data={
            "connection-protocol": "abfss",
            "secret-key": "mysecret",
            "container": "mycontainer",
            "storage-account": "myaccount",
            "path": "mypath",
        },
    )

    azure_credentials = {
        "secret-key": "mysecret",
        "container": "mycontainer",
        "storage-account": "myaccount",
        "endpoint": "myendpoint",
        "path": "mypath",
    }

    # ensure backup cannot be created if run on non-leader unit
    state_in = testing.State(relations={peer_relation, azure_relation}, leader=False)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("create-backup"), state_in)

        assert e.message == "Action must be performed on the leader unit."

    # ensure action fails if snapshot in etcd cannot be created
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"azure-credentials": json.dumps(azure_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(
        secrets=[secret], relations={peer_relation, azure_relation}, leader=True
    )
    with (
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="snapshot save")),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("create-backup"), state_in)

            assert e.message == "Failed to create database backup."

    # ensure action fails if backup already in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started"},
        local_app_data={"backup_id": "xyz"},
    )
    secret_content = {"azure-credentials": json.dumps(azure_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(
        secrets=[secret], relations={peer_relation, azure_relation}, leader=True
    )
    with patch("subprocess.run"):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("create-backup"), state_in)

            assert e.message == "There is currently a backup in progress, please wait."

    # happy path
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"azure-credentials": json.dumps(azure_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(
        secrets=[secret], relations={peer_relation, azure_relation}, leader=True
    )
    with patch("managers.backup.BackupManager.create_backup", return_value="my_backup_id"):
        ctx.run(ctx.on.action("create-backup"), state_in)

    assert ctx.action_results == {"backup-id": "my_backup_id"}


def test_support_only_one_object_storage():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    azure_credentials = {
        "secret-key": "mysecret",
        "container": "mycontainer",
        "storage-account": "myaccount",
        "endpoint": "myendpoint",
        "path": "mypath",
    }
    azure_relation = testing.Relation(
        id=2,
        interface="azure",
        endpoint=AZURE_RELATION_NAME,
        remote_app_name="azure",
        remote_app_data={
            "connection-protocol": "abfss",
            "secret-key": "mysecret",
            "container": "mycontainer",
            "storage-account": "myaccount",
            "path": "mypath",
        },
    )
    s3_credentials = {
        "access-key": "mykey",
        "secret-key": "mysecret",
        "bucket": "mybucket",
        "endpoint": "myendpoint",
        "path": "mypath",
    }
    s3_relation = testing.Relation(
        id=3,
        interface="s3",
        endpoint=S3_RELATION_NAME,
        remote_app_name="s3",
        remote_app_data=s3_credentials,
    )

    # ensure charm is in blocked status if both s3 and azure are related
    with (
        patch("managers.backup.BackupManager.create_container"),
        patch("managers.backup.BackupManager.create_bucket"),
    ):
        secret_content = {
            "azure-credentials": json.dumps(azure_credentials),
            "s3-credentials": json.dumps(s3_credentials),
        }
        secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
        state_in = testing.State(
            relations={peer_relation, azure_relation, s3_relation},
            secrets=[secret],
            leader=True,
        )
        state_out = ctx.run(ctx.on.relation_changed(s3_relation), state_in)
        assert state_out.unit_status == ops.BlockedStatus(
            "Azure and S3 storages configured - please remove one"
        )

    # ensure backup cannot be created if both s3 and azure are related
    state_in = testing.State(relations={peer_relation, azure_relation, s3_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("create-backup"), state_in)

        assert e.message == "Azure and S3 storages configured - please remove one."

    # ensure backups cannot be listed if both s3 and azure are related
    state_in = testing.State(relations={peer_relation, azure_relation, s3_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("list-backups"), state_in)

        assert e.message == "Azure and S3 storages configured - please remove one."

    # ensure backup cannot be restored if both s3 and azure are related
    state_in = testing.State(relations={peer_relation, azure_relation, s3_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("restore"), state_in)

        assert e.message == "Azure and S3 storages configured - please remove one."


def test_ensure_at_least_one_object_storage():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    # ensure backup cannot be created if none of s3 or azure are related
    state_in = testing.State(relations={peer_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("create-backup"), state_in)

        assert e.message == "No object storage configured - please add Azure or S3 relation."

    # ensure backups cannot be listed if none of s3 or azure are related
    state_in = testing.State(relations={peer_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("list-backups"), state_in)

        assert e.message == "No object storage configured - please add Azure or S3 relation."

    # ensure backup cannot be restored if none of s3 or azure are related
    state_in = testing.State(relations={peer_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("restore"), state_in)

        assert e.message == "No object storage configured - please add Azure or S3 relation."


def test_list_backups_action_s3():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    s3_credentials = {
        "access-key": "mykey",
        "secret-key": "mysecret",
        "bucket": "mybucket",
        "endpoint": "myendpoint",
        "path": "mypath",
    }
    s3_relation = testing.Relation(
        id=2,
        interface="s3",
        endpoint=S3_RELATION_NAME,
        remote_app_name="s3",
        remote_app_data=s3_credentials,
    )

    # ensure action fails on non-leader unit
    state_in = testing.State(relations={peer_relation, s3_relation}, leader=False)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("list-backups"), state_in)

        assert e.message == "Action must be performed on the leader unit."

    # ensure action fails if no s3 relation
    state_in = testing.State(relations={peer_relation, s3_relation}, leader=True)
    with patch("workload.EtcdWorkload.alive", return_value=True):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("list-backups"), state_in)

            assert e.message == "No credentials for object storage available."

    # happy path
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    backup_list = ["2025-03-19T11:56:30Z", "2025-03-19T11:57:52Z"]
    expected_output = [
        "backup-id             | backup-status",
        "-------------------------------------",
        "2025-03-19T11:56:30Z  | finished",
        "2025-03-19T11:57:52Z  | finished",
    ]

    state_in = testing.State(secrets=[secret], relations={peer_relation, s3_relation}, leader=True)
    with patch("managers.backup.BackupManager.list_backups", return_value=backup_list):
        ctx.run(ctx.on.action("list-backups"), state_in)

    assert ctx.action_results == {"backups": "\n".join(expected_output)}


def test_list_backups_action_azure():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    azure_relation = testing.Relation(
        id=2,
        interface="azure",
        endpoint=AZURE_RELATION_NAME,
        remote_app_name="azure",
        remote_app_data={
            "connection-protocol": "abfss",
            "secret-key": "mysecret",
            "container": "mycontainer",
            "storage-account": "myaccount",
            "path": "mypath",
        },
    )

    azure_credentials = {
        "secret-key": "mysecret",
        "container": "mycontainer",
        "storage-account": "myaccount",
        "endpoint": "myendpoint",
        "path": "mypath",
    }

    # happy path
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"azure-credentials": json.dumps(azure_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    backup_list = ["2025-03-19T11:56:30Z", "2025-03-19T11:57:52Z"]
    expected_output = [
        "backup-id             | backup-status",
        "-------------------------------------",
        "2025-03-19T11:56:30Z  | finished",
        "2025-03-19T11:57:52Z  | finished",
    ]

    state_in = testing.State(
        secrets=[secret], relations={peer_relation, azure_relation}, leader=True
    )
    with patch("managers.backup.BackupManager.list_backups", return_value=backup_list):
        ctx.run(ctx.on.action("list-backups"), state_in)

    assert ctx.action_results == {"backups": "\n".join(expected_output)}


def test_restore_action_s3():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    s3_credentials = {
        "access-key": "mykey",
        "secret-key": "mysecret",
        "bucket": "mybucket",
        "endpoint": "myendpoint",
        "path": "mypath",
    }
    s3_relation = testing.Relation(
        id=2,
        interface="s3",
        endpoint=S3_RELATION_NAME,
        remote_app_name="s3",
        remote_app_data=s3_credentials,
    )

    # ensure action fails on non-leader unit
    state_in = testing.State(relations={peer_relation, s3_relation}, leader=False)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("restore"), state_in)

        assert e.message == "Action must be performed on the leader unit."

    # ensure action fails if no s3 relation
    state_in = testing.State(relations={peer_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("restore"), state_in)

        assert e.message == "No credentials for object storage available."

    # ensure action fails if unit not started
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(secrets=[secret], relations={peer_relation, s3_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("restore"), state_in)

        assert e.message == "Database is not started, cannot perform backup action."

    # ensure action fails if another restore is already running
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started"},
        local_app_data={"restore_id": "XYZ"},
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(secrets=[secret], relations={peer_relation, s3_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("restore"), state_in)

        assert e.message == "Restore is already in progress."

    # ensure action fails if no backup-id provided to restore
    secret_key = "root"
    secret_value = "123"
    secret_content = {secret_key: secret_value}
    admin_secret = testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(
        secrets=[secret],
        relations={peer_relation, s3_relation},
        leader=True,
        config={INTERNAL_USER_PASSWORD_CONFIG: admin_secret.id},
    )
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("restore"), state_in)

        assert e.message == "Must provide backup-id to restore."

    # action should fail if download of backup-file fails
    backup_id = "xyz"
    secret_key = "root"
    secret_value = "123"
    secret_content = {secret_key: secret_value}
    admin_secret = testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(
        secrets=[secret],
        relations={peer_relation, s3_relation},
        leader=True,
        config={INTERNAL_USER_PASSWORD_CONFIG: admin_secret.id},
    )

    with (
        patch("managers.backup.BackupManager.download_backup_file", return_value=False),
        patch("managers.backup.BackupManager.list_backups", return_value=["xyz", "abc"]),
    ):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("restore", params={"backup-id": backup_id}), state_in)

            assert e.message == f"Could not download backup-file {backup_id}."

    # action should fail if backup-id doesn't exist
    backup_id = "xyz"
    secret_key = "root"
    secret_value = "123"
    secret_content = {secret_key: secret_value}
    admin_secret = testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(
        secrets=[secret],
        relations={peer_relation, s3_relation},
        leader=True,
        config={INTERNAL_USER_PASSWORD_CONFIG: admin_secret.id},
    )

    with (
        patch("managers.backup.BackupManager.download_backup_file", return_value=False),
        patch("managers.backup.BackupManager.list_backups", return_value=["abc"]),
    ):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("restore", params={"backup-id": backup_id}), state_in)

            assert e.message == "Backup ID not found."

    # happy path
    backup_id = "xyz"
    secret_key = "root"
    secret_value = "123"
    secret_content = {secret_key: secret_value}
    admin_secret = testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started"},
        local_app_data={"restore_verification_failed": "True"},
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(
        secrets=[secret],
        relations={peer_relation, s3_relation},
        leader=True,
        config={INTERNAL_USER_PASSWORD_CONFIG: admin_secret.id},
    )

    with (
        patch("managers.backup.BackupManager.download_backup_file", return_value=True),
        patch("managers.backup.BackupManager.list_backups", return_value=["xyz", "abc"]),
    ):
        state_out = ctx.run(ctx.on.action("restore", params={"backup-id": backup_id}), state_in)

        assert ctx.action_results == {"success": f"restore initiated for {backup_id}"}
    assert state_out.get_relation(1).local_app_data.get("restore_id") == backup_id
    assert (
        state_out.get_relation(1).local_app_data.get("restore_instruction")
        == RestoreStep.DOWNLOAD.value
    )


def test_restore_action_azure():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    azure_relation = testing.Relation(
        id=2,
        interface="azure",
        endpoint=AZURE_RELATION_NAME,
        remote_app_name="azure",
        remote_app_data={
            "connection-protocol": "abfss",
            "secret-key": "mysecret",
            "container": "mycontainer",
            "storage-account": "myaccount",
            "path": "mypath",
        },
    )

    azure_credentials = {
        "secret-key": "mysecret",
        "container": "mycontainer",
        "storage-account": "myaccount",
        "endpoint": "myendpoint",
        "path": "mypath",
    }

    # happy path
    backup_id = "xyz"
    secret_key = "root"
    secret_value = "123"
    secret_content = {secret_key: secret_value}
    admin_secret = testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"azure-credentials": json.dumps(azure_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(
        secrets=[secret],
        relations={peer_relation, azure_relation},
        leader=True,
        config={INTERNAL_USER_PASSWORD_CONFIG: admin_secret.id},
    )

    with (
        patch("managers.backup.BackupManager.download_backup_file", return_value=True),
        patch("managers.backup.BackupManager.list_backups", return_value=["xyz"]),
    ):
        state_out = ctx.run(ctx.on.action("restore", params={"backup-id": backup_id}), state_in)

        assert ctx.action_results == {"success": f"restore initiated for {backup_id}"}
    assert state_out.get_relation(1).local_app_data.get("restore_id") == backup_id
    assert (
        state_out.get_relation(1).local_app_data.get("restore_instruction")
        == RestoreStep.DOWNLOAD.value
    )


def test_restore_workflow_order():
    ctx = testing.Context(EtcdOperatorCharm)
    # dummy context for using the backup manager
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(relations={relation})
    with patch("workload.EtcdWorkload.install"):
        with ctx(ctx.on.install(), state_in) as context:
            assert (
                context.charm.backup_manager.next_restore_step(
                    current_step=RestoreStep.NOT_STARTED
                )
                == RestoreStep.DOWNLOAD
            )
            assert (
                context.charm.backup_manager.next_restore_step(current_step=RestoreStep.DOWNLOAD)
                == RestoreStep.STOP
            )
            assert (
                context.charm.backup_manager.next_restore_step(current_step=RestoreStep.STOP)
                == RestoreStep.VERIFY
            )
            assert (
                context.charm.backup_manager.next_restore_step(current_step=RestoreStep.VERIFY)
                == RestoreStep.RESTORE
            )
            assert (
                context.charm.backup_manager.next_restore_step(current_step=RestoreStep.RESTORE)
                == RestoreStep.START
            )
            assert (
                context.charm.backup_manager.next_restore_step(current_step=RestoreStep.START)
                == RestoreStep.COMPLETED
            )


def test_restore_workflow_synchronization():
    ctx = testing.Context(EtcdOperatorCharm)

    # restore step: stop (non-leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.DOWNLOAD.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.STOP.value,
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=False)
    with (
        patch("workload.EtcdWorkload.stop"),
        patch("workload.EtcdWorkload.disable_service"),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        assert state_out.unit_status == ops.MaintenanceStatus("Database restore is in progress")
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step") == RestoreStep.STOP.value
        )

    # restore step: stop (leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.DOWNLOAD.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.STOP.value,
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.stop"),
        patch("workload.EtcdWorkload.disable_service"),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        assert state_out.unit_status == ops.MaintenanceStatus("Database restore is in progress")
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step") == RestoreStep.STOP.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("restore_instruction")
            == RestoreStep.VERIFY.value
        )

    # restore step: verify (non-leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.STOP.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.VERIFY.value,
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=False)
    state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

    assert state_out.unit_status == ops.MaintenanceStatus("Database restore is in progress")
    assert (
        state_out.get_relation(1).local_unit_data.get("restore_step") == RestoreStep.VERIFY.value
    )

    # restore step: verify (leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.STOP.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.VERIFY.value,
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.remove_directory"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="")),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.enable_service"),
        patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
        patch("workload.EtcdWorkload.stop"),
        patch("workload.EtcdWorkload.disable_service"),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        assert state_out.unit_status == ops.MaintenanceStatus("Database restore is in progress")
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step")
            == RestoreStep.VERIFY.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("restore_instruction")
            == RestoreStep.RESTORE.value
        )

    # restore step: verify (leader) -> failed
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.STOP.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.VERIFY.value,
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.remove_directory"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="")),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.enable_service"),
        patch("managers.cluster.ClusterManager.is_healthy", return_value=False),
        patch("workload.EtcdWorkload.stop"),
        patch("workload.EtcdWorkload.disable_service"),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        assert state_out.unit_status == ops.BlockedStatus(
            "Restore verification failed - etcd cluster still running, restore cancelled, check debug-log"
        )
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step")
            == RestoreStep.VERIFY.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("restore_instruction")
            == RestoreStep.RESTORE.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("restore_verification_failed") == "True"
        )

    # restore step: restore (non-leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.VERIFY.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.RESTORE.value,
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=False)
    with (
        patch("workload.EtcdWorkload.remove_directory"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="")),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step")
            == RestoreStep.RESTORE.value
        )

    # restore step: restore (leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.VERIFY.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.RESTORE.value,
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.remove_directory"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="")),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step")
            == RestoreStep.RESTORE.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("restore_instruction")
            == RestoreStep.START.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("cluster_state")
            == EtcdClusterState.NEW.value
        )

    # restore step: skip restore after verification failed (non-leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.VERIFY.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.RESTORE.value,
            "restore_verification_failed": "True",
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=False)
    with (
        patch("managers.backup.BackupManager.restore_backup") as restore_backup,
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        restore_backup.assert_not_called()
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step")
            == RestoreStep.RESTORE.value
        )
        assert state_out.unit_status == ops.BlockedStatus(
            "Restore verification failed - etcd cluster still running, restore cancelled, check debug-log"
        )

    # restore step: skip restore after verification failed (leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.VERIFY.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.RESTORE.value,
            "restore_verification_failed": "True",
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("managers.backup.BackupManager.restore_backup") as restore_backup,
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        restore_backup.assert_not_called()
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step")
            == RestoreStep.RESTORE.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("restore_instruction")
            == RestoreStep.START.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("cluster_state")
            == EtcdClusterState.EXISTING.value
        )
        assert state_out.unit_status == ops.BlockedStatus(
            "Restore verification failed - etcd cluster still running, restore cancelled, check debug-log"
        )

    # restore step: restore -> failed
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.VERIFY.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.RESTORE.value,
            "cluster_state": EtcdClusterState.EXISTING.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation})
    with (
        patch("workload.EtcdWorkload.remove_directory"),
        patch(
            "subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="snapshot restore")
        ),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)
        assert not (
            state_out.get_relation(1).local_app_data.get("cluster_state")
            == EtcdClusterState.NEW.value
        )
        assert state_out.unit_status == ops.BlockedStatus("failed to restore backup")

    # restore step: restart (non-leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.RESTORE.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.START.value,
            "cluster_state": EtcdClusterState.NEW.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=False)
    with (
        patch("workload.EtcdWorkload.write_file") as write_config,
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.enable_service"),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        write_config.assert_called_once()
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step")
            == RestoreStep.START.value
        )

    # restore step: restart (leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.RESTORE.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.START.value,
            "cluster_state": EtcdClusterState.NEW.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.write_file") as write_config,
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.enable_service"),
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="user add")),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        write_config.assert_called_once()
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step")
            == RestoreStep.START.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("restore_instruction")
            == RestoreStep.COMPLETED.value
        )

    # restore step: clean up (non-leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.START.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.COMPLETED.value,
            "cluster_state": EtcdClusterState.NEW.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=False)
    with (
        patch("workload.EtcdWorkload.remove_file") as remove_backup,
        patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        remove_backup.assert_called_once()
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step", "")
            == RestoreStep.NOT_STARTED.value
        )

    # restore step: clean up (non-leader) -> unhealthy
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.START.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.COMPLETED.value,
            "cluster_state": EtcdClusterState.NEW.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=False)
    with (
        patch("workload.EtcdWorkload.remove_file") as remove_backup,
        patch("managers.cluster.ClusterManager.is_healthy", return_value=False),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        assert state_out.unit_status == ops.BlockedStatus(
            "cluster unhealthy after restoring backup - check debug-log"
        )

    # restore step: clean up (leader)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.START.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.COMPLETED.value,
            "cluster_state": EtcdClusterState.NEW.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.remove_file") as remove_backup,
        patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        remove_backup.assert_called_once()
        assert (
            state_out.get_relation(1).local_unit_data.get("restore_step", "")
            == RestoreStep.NOT_STARTED.value
        )
        assert (
            state_out.get_relation(1).local_app_data.get("restore_instruction", "")
            == RestoreStep.NOT_STARTED.value
        )
        assert state_out.get_relation(1).local_app_data.get("restore_id", "") == ""
        assert (
            state_out.get_relation(1).local_app_data.get("cluster_state")
            == EtcdClusterState.EXISTING.value
        )

    # restore step: clean up (leader) -> unhealthy
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"state": "started", "restore_step": RestoreStep.START.value},
        local_app_data={
            "restore_id": "xyz",
            "restore_instruction": RestoreStep.COMPLETED.value,
            "cluster_state": EtcdClusterState.NEW.value,
            "authentication": "enabled",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.remove_file") as remove_backup,
        patch("managers.cluster.ClusterManager.is_healthy", return_value=False),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)

        assert state_out.unit_status == ops.BlockedStatus(
            "cluster unhealthy after restoring backup - check debug-log"
        )
