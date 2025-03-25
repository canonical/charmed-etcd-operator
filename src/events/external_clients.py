#!/usr/bin/env python3
# Copyright 2025 Canonical Limited
# See LICENSE file for licensing details.

"""External clients related event handlers."""

import logging
from typing import TYPE_CHECKING

from charms.certificate_transfer_interface.v1.certificate_transfer import (
    CertificatesAvailableEvent,
    CertificatesRemovedEvent,
    CertificateTransferRequires,
)
from charms.data_platform_libs.v0.data_interfaces import (
    EtcdProvides,
    MTLSChainUpdatedEvent,
)
from ops import Object, RelationBrokenEvent

from literals import (
    CERTIFICATE_TRANSFER_INTERFACE,
    EXTERNAL_CLIENTS_RELATION,
    Status,
    TLSCARotationState,
    TLSState,
    TLSType,
)

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class ExternalClientsEvents(Object):
    """Handle all base and etcd related events."""

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, key="etcd_events")
        self.charm = charm

        self.etcd_provides = EtcdProvides(self.charm, EXTERNAL_CLIENTS_RELATION)

        self.certificate_transfer = CertificateTransferRequires(
            self.charm, CERTIFICATE_TRANSFER_INTERFACE
        )
        self.framework.observe(
            self.certificate_transfer.on.certificate_set_updated, self._on_certificates_available
        )
        self.framework.observe(
            self.certificate_transfer.on.certificates_removed, self._on_certificates_removed
        )

        self.framework.observe(
            self.etcd_provides.on.mtls_chain_updated, self._on_mtls_chain_updated
        )
        self.framework.observe(
            self.charm.on[EXTERNAL_CLIENTS_RELATION].relation_broken, self._on_relation_broken
        )

    def _on_mtls_chain_updated(self, event: MTLSChainUpdatedEvent):  # noqa: C901
        """Handle the ca chain updated event."""
        if not event.mtls_chain or not event.prefix:
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

        # Get common name from mtls_chain
        old_common_name = None
        if self.charm.state.cluster.managed_users.get(event.relation.id):
            old_common_name = (
                self.charm.external_clients_manager.get_common_name_from_chain(
                    event.old_mtls_chain
                )
                if event.old_mtls_chain
                else None
            )
        common_name = self.charm.external_clients_manager.get_common_name_from_chain(
            event.mtls_chain
        )

        # validate leaf certificate
        if not self.charm.external_clients_manager.is_leaf_certificate_valid(event.mtls_chain):
            logger.error("Invalid end-entity certificate")
            # clean the old user if exists
            if old_common_name:
                self._on_relation_broken(event)  # type: ignore
                return
            return

        # if leader then create/update user
        if self.charm.unit.is_leader():
            if old_common_name != common_name:
                logger.debug(f"Common name changed from {old_common_name} to {common_name}")

                if old_common_name:
                    logger.warning("Removing relation's old user")
                    self.charm.cluster_manager.remove_managed_user(old_common_name)
                    self.charm.external_clients_manager.remove_managed_user(event.relation.id)

                if self.charm.cluster_manager.get_user(common_name) is not None:
                    logger.error("User already exists")
                    # TODO set blocked status based on DP blocked states
                    return

                logger.info("Creating new user")
                self.charm.cluster_manager.add_managed_user(common_name, event.prefix)
                self.charm.external_clients_manager.add_managed_user(
                    event.relation.id, common_name
                )
                self.etcd_provides.set_credentials(event.relation.id, common_name, "")
                self.charm.external_clients_events.update_client_relations_data()

        relation_managed_user = self.charm.external_clients_manager.get_relation_managed_user(
            event.relation.id
        )

        if relation_managed_user != common_name:
            logger.error("New user not created yet")
            event.defer()
            return

        if relation_managed_user and self.charm.tls_manager.is_new_ca(
            event.mtls_chain, TLSType.CLIENT
        ):
            self.charm.tls_events.clean_ca_event.emit(cert_type=TLSType.CLIENT)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle the relation broken event."""
        relation_managed_user = self.charm.external_clients_manager.get_relation_managed_user(
            event.relation.id
        )

        if self.charm.unit.is_leader() and relation_managed_user:
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
        etcd_version = self.charm.cluster_manager.get_version()
        for relation in self.etcd_provides.relations:
            relation_data = self.etcd_provides.fetch_my_relation_data(
                [relation.id], ["endpoints", "tls-ca", "version"]
            )[relation.id]

            if set(relation_data.get("endpoints", "").split(",")) != endpoints:
                self.etcd_provides.set_endpoints(relation.id, ",".join(endpoints))

            if relation_data.get("tls-ca") != server_ca:
                self.etcd_provides.set_tls_ca(relation.id, server_ca)

            if relation_data.get("version") != etcd_version:
                self.etcd_provides.set_version(relation.id, etcd_version)

    def _on_certificates_available(self, event: CertificatesAvailableEvent):
        """Handle the certificates available event."""
        logger.debug("Certificates available event")
        cas = self.certificate_transfer.get_all_certificates()
        if self.certificate_transfer and self.charm.tls_manager.is_new_ca(
            "\n".join(cas), TLSType.CLIENT
        ):
            self.charm.tls_events.clean_ca_event.emit(cert_type=TLSType.CLIENT)

    def _on_certificates_removed(self, event: CertificatesRemovedEvent):
        """Handle the certificates removed event."""
        self.charm.tls_events.clean_ca_event.emit(cert_type=TLSType.CLIENT)

    def compute_component_status(self) -> list[Status]:
        """Compute the component status."""
        status_list = []

        for relation in self.etcd_provides.relations:
            mtls_chain = self.etcd_provides.fetch_relation_field(relation.id, "mtls-chain")
            if not mtls_chain:
                continue
            if not self.charm.external_clients_manager.is_leaf_certificate_valid(mtls_chain):
                status_list.append(Status.EC_INVALID_CERTIFICATE)

        return status_list
