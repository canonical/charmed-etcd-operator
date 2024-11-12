#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import ops
import ops.testing
from scenario import Context, State

from charm import EtcdOperatorCharm

def test_start():
    ctx = Context(EtcdOperatorCharm, meta={"name": "my-charm"})
    state_in = State()
    state_out = ctx.run(ctx.on.start(), state_in)
    assert state_out.unit_status == ops.ActiveStatus()

def test_install_failure_blocked_status():
    ctx = Context(EtcdOperatorCharm, meta={"name": "my-charm"})
    state_in = State()

    with patch("workload.EtcdWorkload.install", return_value=False):
        state_out = ctx.run(ctx.on.install(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("unable to install etcd snap")
