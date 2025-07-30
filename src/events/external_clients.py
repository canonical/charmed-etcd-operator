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
    MTLSCertUpdatedEvent,
)
from ops import Object, RelationBrokenEvent

from common.certificates import is_leaf_certificate_valid
from common.exceptions import EtcdUserManagementError
from literals import (
    CERTIFICATE_TRANSFER_RELATION,
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
            self.charm, CERTIFICATE_TRANSFER_RELATION
        )
        self.framework.observe(
            self.certificate_transfer.on.certificate_set_updated, self._on_certificates_available
        )
        self.framework.observe(
            self.certificate_transfer.on.certificates_removed, self._on_certificates_removed
        )

        self.framework.observe(self.etcd_provides.on.mtls_cert_updated, self._on_mtls_cert_updated)
        self.framework.observe(
            self.charm.on[EXTERNAL_CLIENTS_RELATION].relation_broken, self._on_relation_broken
        )

    def _on_mtls_cert_updated(self, event: MTLSCertUpdatedEvent) -> None:  # noqa: C901
        """Handle the ca chain updated event."""
        if not event.mtls_cert or not event.prefix:
            logger.error("CA chain, keys prefix, or common name not provided")
            self.charm.set_status(Status.EC_MISSING_CREDENTIALS)
            return

        if self.charm.state.unit_server.tls_client_state in [TLSState.NO_TLS, TLSState.TO_NO_TLS]:
            logger.error("TLS is not enabled")
            self.charm.set_status(Status.EC_TLS_IS_DISABLED)
            event.defer()
            return

        if self.charm.state.unit_server.tls_client_state == TLSState.TO_TLS:
            logger.error("TLS is not ready")
            self.charm.set_status(Status.TLS_NOT_READY)
            event.defer()
            return

        if (
            self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            logger.debug("CA rotation is in progress")
            self.charm.set_status(Status.TLS_CLIENT_CA_ROTATING)
            event.defer()
            return

        if not self.charm.state.cluster.auth_enabled:
            logger.error("Cluster authentication is not enabled")
            self.charm.set_status(Status.CLUSTER_NOT_INITIALIZED)
            event.defer()
            return

        # Get common name from mtls_cert
        old_common_name = None
        if self.charm.state.cluster.managed_users.get(event.relation.id):
            old_common_name = (
                self.charm.external_clients_manager.get_common_name_from_chain(event.old_mtls_cert)
                if event.old_mtls_cert
                else None
            )
        common_name = self.charm.external_clients_manager.get_common_name_from_chain(
            event.mtls_cert
        )

        # validate leaf certificate
        if not is_leaf_certificate_valid(event.mtls_cert):
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

                # The old username is deleted even if creating the new user fails
                if old_common_name:
                    logger.warning("Removing relation's old user")
                    try:
                        self.charm.cluster_manager.remove_managed_user(old_common_name)
                    except EtcdUserManagementError as e:
                        logger.error(f"Failed to remove old user from etcd: {e}")
                    self.charm.external_clients_manager.remove_managed_user(event.relation.id)

                relation_managed_user = (
                    self.charm.external_clients_manager.get_relation_managed_user(
                        event.relation.id
                    )
                )
                if self.charm.cluster_manager.get_user(common_name) is not None:
                    if common_name == relation_managed_user:
                        logger.debug("User is already being added for this relation")
                    else:
                        logger.error("User already exists")
                        self.charm.set_status(Status.EC_USERNAME_EXISTS)
                    return

                if relation_managed_user is None:
                    logger.info(f"Creating new user: {common_name}")
                    self.charm.cluster_manager.add_managed_user(common_name, event.prefix)
                    self.charm.external_clients_manager.add_managed_user(
                        event.relation.id, common_name
                    )
                    self.etcd_provides.set_credentials(event.relation.id, common_name, "")
                    self.charm.external_clients_manager.update_client_relations_data(
                        etcd_version=self.charm.cluster_manager.get_version()
                    )

        relation_managed_user = self.charm.external_clients_manager.get_relation_managed_user(
            event.relation.id
        )

        if relation_managed_user != common_name:
            logger.error("New user not created yet")
            event.defer()
            return
        self._update_client_truststore()

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle the relation broken event."""
        relation_managed_user = self.charm.external_clients_manager.get_relation_managed_user(
            event.relation.id
        )

        if self.charm.unit.is_leader() and relation_managed_user:
            try:
                self.charm.cluster_manager.remove_managed_user(relation_managed_user)
            except EtcdUserManagementError as e:
                logger.error(f"Failed to remove user from etcd: {e}")
            self.charm.external_clients_manager.remove_managed_user(event.relation.id)

        self._update_client_truststore()

    def _on_certificates_available(self, event: CertificatesAvailableEvent) -> None:
        """Handle the certificates available event."""
        logger.debug("Certificates available event")
        if (
            self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            logger.debug("CA rotation is in progress")
            event.defer()
            return

        if self.certificate_transfer.get_all_certificates():
            self._update_client_truststore()

    def _on_certificates_removed(self, event: CertificatesRemovedEvent) -> None:
        """Handle the certificates removed event."""
        if (
            self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            logger.debug("CA rotation is in progress")
            event.defer()
            return
        self._update_client_truststore()

    def _update_client_truststore(self) -> None:
        """Update the client truststore and Initiate a rolling restart of the cluster."""
        all_cas = self.charm.tls_manager.collect_client_cas()
        if all_cas != self.charm.tls_manager.load_trusted_ca(TLSType.CLIENT):
            logger.debug("CAs have changed, updating client truststore")
            self.charm.tls_manager.update_cas(all_cas, TLSType.CLIENT)
            self.charm.rolling_restart()
