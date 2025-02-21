#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""

import logging

import ops
from charms.data_platform_libs.v0.data_interfaces import EtcdRequires

logger = logging.getLogger(__name__)


class RequirerCharmCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on.start, self._on_start)
        self.etcd_requires = EtcdRequires(
            self,
            "etcd-client",
            "/test/",
            "-----BEGIN CERTIFICATE-----\ntest_ca\n-----END CERTIFICATE-----",
            "test-common-name",
        )
        framework.observe(self.on.update_action, self._on_update_action)

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        self.unit.status = ops.ActiveStatus()

    def _on_update_action(self, event: ops.ActionEvent):
        """Handle update common name action."""
        # client relation
        relation = self.model.get_relation("etcd-client")
        if not relation:
            event.fail("etcd-client relation not found")
            return

        if event.params.get("common-name"):
            self.etcd_requires.relation_data.update_relation_data(
                relation.id, {"common-name": event.params["common-name"]}
            )

        if event.params.get("ca"):
            ca = event.params["ca"].replace("\\n", "\n")
            self.etcd_requires.relation_data.update_relation_data(relation.id, {"ca-chain": ca})

        event.set_results({"message": "databag updated"})


if __name__ == "__main__":  # pragma: nocover
    ops.main(RequirerCharmCharm)
