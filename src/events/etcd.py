#!/usr/bin/env python3
# Copyright 2024 Canonical Limited
# See LICENSE file for licensing details.

"""Etcd related event handlers."""

import logging
from typing import TYPE_CHECKING

import ops
from ops import Object
from ops.charm import (
    LeaderElectedEvent,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationDepartedEvent,
    RelationJoinedEvent,
)

from literals import PEER_RELATION, Status

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class EtcdEvents(Object):
    """Handle all base and etcd related events."""

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, key="etcd_events")
        self.charm = charm

        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.start, self._on_start)
        self.framework.observe(self.charm.on.config_changed, self._on_config_changed)
        """
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_created, self._on_cluster_relation_created
        )
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_joined, self._on_cluster_relation_joined
        )
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_changed, self._on_cluster_relation_changed
        )
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_departed, self._on_cluster_relation_departed
        )
        """
        self.framework.observe(self.charm.on.leader_elected, self._on_leader_elected)

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle install event."""
        install = self.charm.workload.install()
        if not install:
            self.charm.set_status(Status.SERVICE_NOT_INSTALLED)
            event.defer()
            return

    def _on_start(self, event: ops.StartEvent) -> None:
        """Handle start event."""
        self.charm.state.unit_server.update(self.charm.cluster_manager.get_host_mapping())

        self.charm.set_status(Status.ACTIVE)

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config_changed event."""
        pass

    def _on_cluster_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle event received by a new unit when joining the cluster relation."""
        pass

    def _on_cluster_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle all events related to the cluster-peer relation."""
        pass

    def _on_cluster_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle event received by a unit leaves the cluster relation."""
        pass

    def _on_cluster_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle event received by all units when a new unit joins the cluster relation."""
        pass

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle all events in the 'cluster' peer relation."""
        if not self.charm.state.peer_relation:
            self.charm.set_status(Status.NO_PEER_RELATION)
            return
