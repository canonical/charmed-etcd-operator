#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import patch

import yaml
from ops import testing
from pytest import raises
from scenario import Secret

from charm import EtcdOperatorCharm
from literals import PEER_RELATION, S3_RELATION_NAME

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


def test_create_backup_action():
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
    state_in = testing.State(relations={peer_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("create-backup"), state_in)

        assert e.message == "No credentials for object storage available."

    # ensure backup cannot be created if unit not started
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
    with patch(
        "subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="snapshot save")
    ):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("create-backup"), state_in)

            assert e.message == "Failed to create database backup."

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


def test_list_backups_action():
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

    state_in = testing.State(relations={peer_relation, s3_relation}, leader=False)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("list-backups"), state_in)

        assert e.message == "Action must be performed on the leader unit."

    state_in = testing.State(relations={peer_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("list-backups"), state_in)

        assert e.message == "No credentials for object storage available."

    state_in = testing.State(relations={peer_relation, s3_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("list-backups"), state_in)

        assert e.message == "No credentials for object storage available."

    # happy path
    peer_relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_unit_data={"state": "started"}
    )
    secret_content = {"s3-credentials": json.dumps(s3_credentials)}
    secret = Secret(secret_content, label=f"{PEER_RELATION}.{APP_NAME}.app")
    state_in = testing.State(secrets=[secret], relations={peer_relation, s3_relation}, leader=True)
    with patch("managers.backup.BackupManager.create_backup", return_value="my_backup_id"):
        ctx.run(ctx.on.action("list-backups"), state_in)

    assert ctx.action_results == {"result": "successful"}
