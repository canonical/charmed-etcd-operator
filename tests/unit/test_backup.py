#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import patch

import yaml
from ops import testing
from pytest import raises

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
