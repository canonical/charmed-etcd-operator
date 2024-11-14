#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import ops
from ops import testing

from charm import EtcdOperatorCharm


def test_install_failure_blocked_status():
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State()

    with patch("workload.EtcdWorkload.install", return_value=False):
        state_out = ctx.run(ctx.on.install(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("unable to install etcd snap")


def test_start():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint="etcd-cluster", peers_data={1: {}})
    state_in = testing.State(relations={relation})

    with patch("workload.EtcdWorkload.alive", return_value=True):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    with patch("workload.EtcdWorkload.alive", return_value=False):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("etcd service not running")


def test_update_status():
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State()

    with patch("workload.EtcdWorkload.alive", return_value=False):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("etcd service not running")
