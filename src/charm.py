#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed machine operator for etcd."""

import logging

import ops
from ops import StatusBase
from ops.charm import (
    LeaderElectedEvent,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationDepartedEvent,
    RelationJoinedEvent,
)

from core.cluster import ClusterState
from literals import PEER_RELATION, SUBSTRATE, DebugLevel, Status
from managers.cluster import ClusterManager
from workload import EtcdWorkload

logger = logging.getLogger(__name__)


class EtcdOperatorCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, *args):
        super().__init__(*args)
        self.workload = EtcdWorkload()
        self.state = ClusterState(self, substrate=SUBSTRATE)

        # --- MANAGERS ---
        self.cluster_manager = ClusterManager

        # --- CORE EVENTS ---
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on[PEER_RELATION].relation_created, self._on_cluster_relation_created
        )
        self.framework.observe(
            self.on[PEER_RELATION].relation_joined, self._on_cluster_relation_joined
        )
        self.framework.observe(
            self.on[PEER_RELATION].relation_changed, self._on_cluster_relation_changed
        )
        self.framework.observe(
            self.on[PEER_RELATION].relation_departed, self._on_cluster_relation_departed
        )
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle install event."""
        install = self.workload.install()
        if not install:
            self._set_status(Status.SERVICE_NOT_INSTALLED)
            event.defer()
            return

    def _on_start(self, event: ops.StartEvent) -> None:
        """Handle start event."""
        self._set_status(Status.ACTIVE)

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config_changed event."""
        pass

    def _on_cluster_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle event received by a new unit when joining the cluster relation."""
        pass

    def _on_cluster_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle all events related to the cluster-peer relation."""
        self.state.unit_server.update(self.cluster_manager.get_host_mapping())

    def _on_cluster_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle event received by a unit leaves the cluster relation."""
        pass

    def _on_cluster_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle event received by all units when a new unit joins the cluster relation."""
        pass

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle all events in the 'cluster' peer relation."""
        if not self.state.peer_relation:
            self._set_status(Status.NO_PEER_RELATION)
            return

    def _set_status(self, key: Status) -> None:
        """Set charm status."""
        status: StatusBase = key.value.status
        log_level: DebugLevel = key.value.log_level

        getattr(logger, log_level.lower())(status.message)
        self.unit.status = status


if __name__ == "__main__":  # pragma: nocover
    ops.main(EtcdOperatorCharm)  # type: ignore
