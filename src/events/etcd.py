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
from ops.model import ModelError, SecretNotFoundError

from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdUserManagementError,
    RaftLeaderNotFoundError,
)
from common.secrets import get_secret_from_id
from literals import INTERNAL_USER, INTERNAL_USER_PASSWORD_CONFIG, PEER_RELATION, Status

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
        self.framework.observe(self.charm.on.secret_changed, self._on_secret_changed)

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

        if self.charm.unit.is_leader() and not self.charm.state.cluster.auth_enabled:
            try:
                self.charm.cluster_manager.enable_authentication()
                self.charm.state.cluster.update({"authentication": "enabled"})
            except (EtcdAuthNotEnabledError, EtcdUserManagementError) as e:
                logger.error(e)
                self.charm.set_status(Status.AUTHENTICATION_NOT_ENABLED)
                return

        if self.charm.workload.alive():
            self.charm.set_status(Status.ACTIVE)
        else:
            self.charm.set_status(Status.SERVICE_NOT_RUNNING)

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config_changed event."""
        if not self.charm.unit.is_leader():
            return

        if admin_secret_id := self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
            self.update_admin_password(admin_secret_id)

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

    def _on_secret_changed(self, event: ops.SecretChangedEvent) -> None:
        """Handle the secret_changed event."""
        if not self.charm.unit.is_leader():
            return

        if admin_secret_id := self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
            if admin_secret_id == event.secret.id:
                self.update_admin_password(admin_secret_id)

    def update_admin_password(self, admin_secret_id: str) -> None:
        """Compare current admin password and update in etcd if required."""
        try:
            if new_password := get_secret_from_id(self.charm.model, admin_secret_id).get(
                INTERNAL_USER
            ):
                # only update admin credentials if the password has changed
                if new_password != self.charm.state.cluster.internal_user_credentials.get(
                    INTERNAL_USER
                ):
                    logger.debug(f"{INTERNAL_USER_PASSWORD_CONFIG} have changed.")
                    try:
                        self.charm.cluster_manager.update_credentials(
                            username=INTERNAL_USER, password=new_password
                        )
                        self.charm.state.cluster.update(
                            {f"{INTERNAL_USER}-password": new_password}
                        )
                        self.charm.set_status(Status.ACTIVE)
                    except EtcdUserManagementError as e:
                        logger.error(e)
                        self.charm.set_status(Status.PASSWORD_UPDATE_FAILED)
            else:
                logger.warning(f"Invalid username in secret {admin_secret_id}.")
                self.charm.set_status(Status.PASSWORD_UPDATE_FAILED)
        except (ModelError, SecretNotFoundError) as e:
            logger.error(e)
            self.charm.set_status(Status.PASSWORD_UPDATE_FAILED)
