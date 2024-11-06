# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest

from scenario import State, Context
import ops
import ops.testing
from charm import EtcdOperatorCharm


class TestCharm(unittest.TestCase):
    def test_start(self):
        ctx = Context(EtcdOperatorCharm, meta={'name': 'my-charm'})
        state_in = State()
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()
