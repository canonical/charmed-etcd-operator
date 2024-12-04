#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import yaml
from ops import testing
from ops.testing import ActionFailed
from pytest import raises

from charm import EtcdOperatorCharm
from literals import INTERNAL_USER, PEER_RELATION

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def test_get_password():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    # make sure admin credentials are created initially
    state_in = testing.State(relations={relation}, leader=True)
    state_out = ctx.run(ctx.on.leader_elected(), state_in)

    ctx.run(ctx.on.action("get-password", params={"username": f"{INTERNAL_USER}"}), state_out)
    assert ctx.action_results.get("username") == INTERNAL_USER
    assert ctx.action_results.get("password")
    assert ctx.action_results.get("ca-chain")


def test_set_password():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    password = "test_pwd"

    # this action is not allowed on non-leader units
    state_in = testing.State(relations={relation}, leader=False)
    with raises(ActionFailed) as error:
        ctx.run(
            ctx.on.action(
                "set-password", params={"username": INTERNAL_USER, "password": password}
            ),
            state_in,
        )
    assert error.value.message == "Action can only be run on the leader unit."

    # make sure a password cannot set for any other than the admin user
    state_in = testing.State(relations={relation}, leader=True)
    with raises(ActionFailed) as error:
        ctx.run(
            ctx.on.action("set-password", params={"username": "my_user", "password": password}),
            state_in,
        )
    assert error.value.message == f"Action only allowed for user {INTERNAL_USER}."

    # update the admin user's password
    state_in = testing.State(relations={relation}, leader=True)
    with patch(
        "subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="OK")
    ):
        ctx.run(
            ctx.on.action(
                "set-password", params={"username": INTERNAL_USER, "password": password}
            ),
            state_in,
        )
        assert ctx.action_results.get(f"{INTERNAL_USER}-password") == password
