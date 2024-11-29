#!/usr/bin/env python3
# Copyright 2024 Canonical Limited
# See LICENSE file for licensing details.

"""Etcd related and core event handlers."""

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

from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdUserNotCreatedError,
    RaftLeaderNotFoundError,
)
from literals import INTERNAL_USER, PEER_RELATION, Status

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class EtcdEvents(Object):
    """Handle all base and etcd related events."""

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, key="etcd_events")
        self.charm = charm

        # --- Core etcd charm events ---

        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.start, self._on_start)
        self.framework.observe(self.charm.on.config_changed, self._on_config_changed)
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
        self.framework.observe(self.charm.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.charm.on.update_status, self._on_update_status)

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle install event."""
        if not self.charm.workload.install():
            self.charm.set_status(Status.SERVICE_NOT_INSTALLED)
            return

    def _on_start(self, event: ops.StartEvent) -> None:
        """Handle start event."""
        # Make sure all planned units have joined the peer relation before starting the cluster
        if (
            not self.charm.state.peer_relation
            or len(self.charm.state.peer_relation.units) + 1 < self.charm.app.planned_units()
        ):
            logger.info("Deferring start because not all units joined peer-relation.")
            self.charm.set_status(Status.NO_PEER_RELATION)
            event.defer()
            return

        self.charm.config_manager.set_config_properties()

        self.charm.workload.start()

        if self.charm.unit.is_leader():
            try:
                self.charm.cluster_manager.enable_authentication()
            except (
                RaftLeaderNotFoundError,
                EtcdAuthNotEnabledError,
                EtcdUserNotCreatedError,
            ) as e:
                logger.error(e)
                self.charm.set_status(Status.AUTHENTICATION_NOT_ENABLED)
                return

        if self.charm.workload.alive():
            self.charm.set_status(Status.ACTIVE)
        else:
            self.charm.set_status(Status.SERVICE_NOT_RUNNING)

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config_changed event."""
        pass

    def _on_cluster_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle event received by a new unit when joining the cluster relation."""
        self.charm.state.unit_server.update(self.charm.cluster_manager.get_host_mapping())
        if self.charm.unit.is_leader():
            self.charm.state.cluster.update({"initial-cluster-state": "new"})

    def _on_cluster_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle all events related to the cluster-peer relation."""
        pass

    def _on_cluster_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle event received by a unit leaves the cluster relation."""
        pass

    def _on_cluster_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle event received by all units when a new unit joins the cluster relation."""
        # Todo: remove this test at some point, this is just for showcasing that it works :)
        # We will need to perform any HA-related action against the raft leader
        # e.g. add members, trigger leader election, log compaction, etc.
        try:
            raft_leader = self.charm.cluster_manager.get_leader()
            logger.info(f"Raft leader: {raft_leader}")
        except RaftLeaderNotFoundError as e:
            logger.warning(e)

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle all events in the 'cluster' peer relation."""
        if not self.charm.state.peer_relation:
            self.charm.set_status(Status.NO_PEER_RELATION)
            return

        if self.charm.unit.is_leader() and not self.charm.state.cluster.internal_user_credentials:
            self.charm.state.cluster.update(
                {f"{INTERNAL_USER}-password": self.charm.workload.generate_password()}
            )

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Handle update_status event."""
        if not self.charm.workload.alive():
            self.charm.set_status(Status.SERVICE_NOT_RUNNING)
