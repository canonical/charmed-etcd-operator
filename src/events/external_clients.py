#!/usr/bin/env python3
# Copyright 2025 Canonical Limited
# See LICENSE file for licensing details.

"""External clients related event handlers."""

import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v0.data_interfaces import (
    ClientRelationUpdatedEvent,
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

        self.framework.observe(
            self.etcd_provides.on.client_relation_updated, self._on_client_relation_updated
        )
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

        if self.charm.cluster_manager.get_user(event.common_name) is not None:
            logger.error("User already exists")
            # TODO set blocked status based on DP blocked states
            return

        if event.old_common_name:
            logger.warning("Removing relation's old user")
            self.charm.cluster_manager.remove_managed_user(event.old_common_name)
            self.charm.external_clients_manager.remove_managed_user(event.relation.id)

        logger.info("Creating new user")
        self.charm.cluster_manager.add_managed_user(event.common_name, event.keys_prefix)
        self.charm.external_clients_manager.add_managed_user(event.relation.id, event.common_name)
        self.charm.external_clients_events.update_client_relations_data()

    def _on_client_relation_updated(self, event: ClientRelationUpdatedEvent):
        """Handle the ca chain updated event."""
        if not event.ca_chain or not event.keys_prefix or not event.common_name:
            logger.error("CA chain, keys prefix, or common name not provided")
            # TODO set blocked status based on DP blocked states
            event.defer()
            return

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
        relation_managed_user = self.charm.external_clients_manager.get_relation_managed_user(
            event.relation.id
        )

        if relation_managed_user != event.common_name:
            logger.error("New user not created yet")
            event.defer()
            return
        if relation_managed_user and self.charm.tls_manager.is_new_ca(
            event.ca_chain, TLSType.CLIENT
        ):
            self.charm.tls_events.clean_ca_event.emit(cert_type=TLSType.CLIENT)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle the relation broken event."""
        relation_managed_user = self.charm.external_clients_manager.get_relation_managed_user(
            event.relation.id
        )

        if self.charm.unit.is_leader():
            self.charm.cluster_manager.remove_managed_user(relation_managed_user)
            self.charm.external_clients_manager.remove_managed_user(event.relation.id)

        self.charm.tls_events.clean_ca_event.emit(cert_type=TLSType.CLIENT)

    def update_client_relations_data(self):
        """Update the ECR data."""
        if not self.charm.unit.is_leader():
            return

        if not self.etcd_provides.relations:
            return

        endpoints = {server.client_url for server in self.charm.state.servers}
        server_certs, _ = self.charm.tls_events.client_certificate.get_assigned_certificates()
        server_ca = server_certs[0].ca.raw
        etcd_api_version = self.charm.cluster_manager.get_version()
        for relation in self.etcd_provides.relations:
            if set(relation.data[self.charm.app].get("endpoints", "").split(",")) != endpoints:
                self.etcd_provides.set_endpoints(relation.id, ",".join(endpoints))

            if relation.data[self.charm.app].get("ca-chain") != server_ca:
                self.etcd_provides.set_ca_chain(relation.id, server_ca)

            if relation.data[self.charm.app].get("version") != etcd_api_version:
                self.etcd_provides.set_version(relation.id, etcd_api_version)

    def _on_relation_joined(self, _):
        """Add the provider side data to the relation."""
        self.update_client_relations_data()
