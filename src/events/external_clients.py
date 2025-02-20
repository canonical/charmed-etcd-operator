#!/usr/bin/env python3
# Copyright 2025 Canonical Limited
# See LICENSE file for licensing details.

"""External clients related event handlers."""

import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v0.data_interfaces import (
    CommonNameUpdatedEvent,
    EtcdProvides,
)
from ops import Object, RelationBrokenEvent

from literals import EXTERNAL_CLIENTS_RELATION, TLSCARotationState, TLSState, TLSType

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class ExternalClientsEvents(Object):
    """Handle all base and etcd related events."""

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, key="etcd_events")
        self.charm = charm

        self.etcd_provides = EtcdProvides(self.charm, EXTERNAL_CLIENTS_RELATION)

        self.framework.observe(
            self.charm.on[EXTERNAL_CLIENTS_RELATION].relation_joined, self._on_relation_joined
        )

        self.framework.observe(
            self.etcd_provides.on.common_name_updated, self._on_common_name_updated
        )
        self.framework.observe(self.etcd_provides.on.ca_chain_updated, self._on_ca_chain_updated)
        self.framework.observe(
            self.charm.on[EXTERNAL_CLIENTS_RELATION].relation_broken, self._on_relation_broken
        )

    def _on_common_name_updated(self, event: CommonNameUpdatedEvent):
        """Handle the common name updated event."""
        if not event.common_name or not event.keys_prefix or not event.ca_chain:
            logger.error("Common name, keys prefix, or CA chain not provided")
            event.defer()
            return

        if not self.charm.state.unit_server.tls_client_state == TLSState.TLS:
            logger.error("TLS is not enabled")
            event.defer()
            return

        managed_users = self.charm.state.cluster.managed_users
        relation_managed_user = managed_users.get(event.relation.id)

        if self.charm.cluster_manager.get_user(event.common_name) is not None and (
            relation_managed_user is None or relation_managed_user.common_name != event.common_name
        ):
            logger.error("User already exists")
            return

        if relation_managed_user:
            logger.warning("Removing relation's old user")
            self.charm.cluster_manager.remove_role(relation_managed_user.common_name)
            self.charm.cluster_manager.remove_user(relation_managed_user.common_name)
            self.charm.external_clients_manager.remove_managed_user(event.relation.id)

        logger.info("Creating new user")
        self.charm.cluster_manager.add_user(event.common_name)
        self.charm.cluster_manager.add_role(event.common_name)
        self.charm.cluster_manager.grant_role(event.common_name, event.common_name)
        self.charm.cluster_manager.grant_permission(event.common_name, event.keys_prefix)
        self.charm.external_clients_manager.add_managed_user(
            event.relation.id, event.common_name, event.ca_chain
        )
        self.charm.external_clients_events.update_ecr_data()

    def _on_ca_chain_updated(self, event):
        """Handle the ca chain updated event."""
        if not self.charm.state.unit_server.tls_client_state == TLSState.TLS:
            logger.error("TLS is not enabled")
            event.defer()
            return

        if (
            self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            logger.debug("CA rotation is in progress")
            event.defer()
            return

        managed_users = self.charm.state.cluster.managed_users
        relation_managed_user = managed_users.get(event.relation.id)

        if not relation_managed_user:
            logger.warning("User not found. Waiting for common name update")
            event.defer()
            return

        self.charm.external_clients_manager.update_managed_user(
            event.relation.id, relation_managed_user.common_name, event.ca_chain
        )

        self.charm.tls_events.clean_ca_event.emit(cert_type=TLSType.CLIENT)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle the relation broken event."""
        managed_users = self.charm.state.cluster.managed_users
        relation_managed_user = managed_users.get(event.relation.id)

        if self.charm.unit.is_leader():
            self.charm.cluster_manager.remove_role(relation_managed_user.common_name)
            self.charm.cluster_manager.remove_user(relation_managed_user.common_name)
            self.charm.external_clients_manager.remove_managed_user(event.relation.id)
        elif relation_managed_user:
            logger.debug("Waiting for leader to remove managed user")
            event.defer()
            return
        self.charm.tls_events.clean_ca_event.emit(cert_type=TLSType.CLIENT)

    def check_external_client_updates(self):
        """Check if a new external client is added."""
        if (
            not self.charm.state.unit_server.tls_client_state == TLSState.TLS
            or not self.charm.state.unit_server.tls_client_ca_rotation_state
            == TLSCARotationState.NO_ROTATION
        ):
            return

        cas_stored = self.charm.tls_manager.load_trusted_ca(TLSType.CLIENT)

        collected_cas = self.charm.tls_events.collect_client_cas()

        if set(collected_cas) - set(cas_stored):
            logger.error("New external client detected. Triggering CA chain update")
            self.charm.tls_events.clean_ca_event.emit(cert_type=TLSType.CLIENT)

    def update_ecr_data(self):
        """Update the ECR data."""
        if not self.charm.unit.is_leader():
            return

        if not self.etcd_provides.relations:
            return

        endpoints = ",".join([server.client_url for server in self.charm.state.servers])
        server_certs, _ = self.charm.tls_events.client_certificate.get_assigned_certificates()
        server_ca = server_certs[0].ca.raw
        for relation in self.etcd_provides.relations:
            self.etcd_provides.set_endpoints(relation.id, endpoints)
            self.etcd_provides.set_ca_chain(relation.id, server_ca)
            self.etcd_provides.set_version(relation.id, self.charm.cluster_manager.get_version())

    def _on_relation_joined(self, _):
        """Add the provider side data to the relation."""
        self.update_ecr_data()
