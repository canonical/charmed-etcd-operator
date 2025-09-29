#!/usr/bin/env python3
# Copyright 2024 Canonical Limited
# See LICENSE file for licensing details.

"""TLS related event handlers."""

import logging
from typing import TYPE_CHECKING

from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    TLSCertificatesRequiresV4,
)
from ops import (
    ConfigChangedEvent,
    EventSource,
    Handle,
    RelationBrokenEvent,
    RelationCreatedEvent,
    SecretChangedEvent,
)
from ops.framework import EventBase, Object

from literals import (
    CLIENT_TLS_RELATION_NAME,
    PEER_TLS_RELATION_NAME,
    TLS_CLIENT_PRIVATE_KEY_CONFIG,
    TLS_PEER_PRIVATE_KEY_CONFIG,
    TLSCARotationState,
    TLSState,
    TLSType,
)
from statuses import TLSStatuses

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class CleanCAEvent(EventBase):
    """Event for cleaning up old CAs."""

    def __init__(
        self,
        handle: Handle,
        cert_type: TLSType,
    ):
        super().__init__(handle)
        self.cert_type = cert_type

    def snapshot(self) -> dict[str, str]:
        """Snapshot of lock event."""
        return {"cert_type": self.cert_type.value}

    def restore(self, snapshot: dict[str, str]) -> None:
        """Restores lock event."""
        self.cert_type = TLSType(snapshot["cert_type"])


class RefreshTLSCertificatesEvent(EventBase):
    """Event for refreshing peer TLS certificates."""


class TLSEvents(Object):
    """Event handlers for related applications on the `certificates` relation interface."""

    clean_ca_event = EventSource(CleanCAEvent)
    refresh_tls_certificates_event = EventSource(RefreshTLSCertificatesEvent)

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, "tls")
        self.charm: "EtcdOperatorCharm" = charm
        common_name = f"{self.charm.unit.name.replace('/', '')}-{self.charm.model.uuid}"
        peer_private_key = None
        client_private_key = None

        if peer_private_key_id := self.charm.config.get(TLS_PEER_PRIVATE_KEY_CONFIG):
            if (
                peer_private_key := self.charm.tls_manager.read_and_validate_private_key(
                    peer_private_key_id
                )
            ) is None:
                self.charm.state.statuses.add(
                    TLSStatuses.TLS_INVALID_PRIVATE_KEY.value,
                    scope="unit",
                    component=self.charm.tls_manager.name,
                )
            else:
                self.charm.state.statuses.delete(
                    TLSStatuses.TLS_INVALID_PRIVATE_KEY.value,
                    scope="unit",
                    component=self.charm.tls_manager.name,
                )

        if client_private_key_id := self.charm.config.get(TLS_CLIENT_PRIVATE_KEY_CONFIG):
            if (
                client_private_key := self.charm.tls_manager.read_and_validate_private_key(
                    client_private_key_id
                )
            ) is None:
                self.charm.state.statuses.add(
                    TLSStatuses.TLS_INVALID_PRIVATE_KEY.value,
                    scope="unit",
                    component=self.charm.tls_manager.name,
                )
            else:
                self.charm.state.statuses.delete(
                    TLSStatuses.TLS_INVALID_PRIVATE_KEY.value,
                    scope="unit",
                    component=self.charm.tls_manager.name,
                )

        self.peer_certificate = TLSCertificatesRequiresV4(
            self.charm,
            PEER_TLS_RELATION_NAME,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=self.charm.tls_manager.build_common_name(
                        common_name, TLSType.PEER
                    ),
                    sans_ip=self.charm.tls_manager.build_sans_ip(TLSType.PEER),
                    sans_dns=self.charm.tls_manager.build_sans_dns(TLSType.PEER),
                ),
            ],
            private_key=peer_private_key,
            refresh_events=[self.refresh_tls_certificates_event],
        )
        self.client_certificate = TLSCertificatesRequiresV4(
            self.charm,
            CLIENT_TLS_RELATION_NAME,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=self.charm.tls_manager.build_common_name(
                        common_name, TLSType.CLIENT
                    ),
                    sans_ip=self.charm.tls_manager.build_sans_ip(TLSType.CLIENT),
                    sans_dns=self.charm.tls_manager.build_sans_dns(TLSType.CLIENT),
                ),
            ],
            private_key=client_private_key,
            refresh_events=[self.refresh_tls_certificates_event],
        )

        self.framework.observe(self.clean_ca_event, self._on_clean_ca)

        for relation in [self.peer_certificate, self.client_certificate]:
            self.framework.observe(
                relation.on.certificate_available, self._on_certificate_available
            )

        for relation in [PEER_TLS_RELATION_NAME, CLIENT_TLS_RELATION_NAME]:
            self.framework.observe(
                self.charm.on[relation].relation_created, self._on_relation_created
            )
            self.framework.observe(
                self.charm.on[relation].relation_broken, self._on_certificates_broken
            )
        self.framework.observe(self.charm.on.config_changed, self._on_config_changed)
        self.framework.observe(self.charm.on.secret_changed, self._on_secret_changed)

    def _on_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle the `relation-created` event.

        Args:
            event (RelationCreatedEvent): The event object.
        """
        if self.charm.refresh_in_progress:
            logger.warning("Cannot enable TLS while refresh is in progress")
            event.defer()
            return

        if event.relation.name == PEER_TLS_RELATION_NAME:
            self.charm.tls_manager.set_tls_state(state=TLSState.TO_TLS, tls_type=TLSType.PEER)
        else:
            self.charm.tls_manager.set_tls_state(state=TLSState.TO_TLS, tls_type=TLSType.CLIENT)

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:  # noqa: C901
        """Handle the `certificates-available` event.

        Args:
            event (CertificateAvailableEvent): The event object.
        """
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
        ):
            logger.warning(
                "Cannot update certificates while cluster is in vulnerable state because of restore or cluster-rebuild"
            )
            event.defer()
            return

        cert = event.certificate

        client_certificates, client_private_key = (
            self.client_certificate.get_assigned_certificates()
        )
        peer_certificates, peer_private_key = self.peer_certificate.get_assigned_certificates()

        try:
            if client_certificates and client_certificates[0].certificate == cert:
                cert_type = TLSType.CLIENT
                cert = client_certificates[0]
                private_key = client_private_key
                tls_state = self.charm.state.unit_server.tls_client_state
                tls_ca_rotation_state = self.charm.state.unit_server.tls_client_ca_rotation_state
            elif peer_certificates and peer_certificates[0].certificate == cert:
                cert_type = TLSType.PEER
                cert = peer_certificates[0]
                private_key = peer_private_key
                tls_state = self.charm.state.unit_server.tls_peer_state
                tls_ca_rotation_state = self.charm.state.unit_server.tls_peer_ca_rotation_state
            else:
                logger.error(
                    f"Received certificate does not match any assigned certificates: {cert}"
                )
                return
        except IndexError:
            logger.error(f"Received certificate does not match any assigned certificates: {cert}")
            return

        logger.debug(f"Received certificate for {cert_type}")

        if (
            tls_state == TLSState.TLS
            and self.charm.tls_manager.is_new_ca(cert.ca.raw, cert_type)
            and tls_ca_rotation_state == TLSCARotationState.NO_ROTATION
        ):
            if self.charm.refresh_in_progress:
                logger.warning("Cannot update CA certificates while refresh is in progress")
                event.defer()
                return
            logger.debug(f"New {cert_type} CA detected, updating trusted CAs")
            self.charm.tls_manager.add_trusted_ca(cert.ca.raw, cert_type)
            self.charm.tls_manager.set_ca_rotation_state(
                cert_type, TLSCARotationState.NEW_CA_DETECTED
            )
            self.charm.rolling_restart("_restart_ca_rotation")
            event.defer()
            return

        # writing certificate after CA rotation
        if tls_ca_rotation_state in [
            TLSCARotationState.NEW_CA_DETECTED,
            TLSCARotationState.NEW_CA_ADDED,
        ]:
            if not self.charm.tls_manager.is_new_ca_saved_on_all_servers(cert_type):
                logger.debug("Waiting for all servers to update CA")
                event.defer()
                return

        # write certificates to disk
        self.charm.tls_manager.write_certificate(cert, private_key, cert_type)  # type: ignore
        # if there are client relations add their CAs to the trusted client CAs
        if cert_type == TLSType.CLIENT and tls_state == TLSState.TO_TLS:
            self.charm.tls_manager.update_cas(
                self.charm.tls_manager.collect_client_cas(), cert_type
            )

        # TLS is enabled, New CA added to all servers, and cert updated -> no rolling restart needed until we clean up old CA
        if tls_state == TLSState.TLS and tls_ca_rotation_state == TLSCARotationState.NEW_CA_ADDED:
            logger.debug(f"Updating {cert_type.value} certificates with new CA")
            self.charm.tls_manager.set_ca_rotation_state(
                cert_type, TLSCARotationState.CERT_UPDATED
            )
            # Update the CA for external clients
            if cert_type == TLSType.CLIENT:
                if self.charm.unit.is_leader():
                    try:
                        self.charm.external_clients_manager.update_client_relations_data(
                            etcd_version=self.charm.cluster_manager.get_version()
                        )
                    except KeyError as e:
                        logger.warning(f"Error updating client relations data: {e}")
            self.clean_ca_event.emit(cert_type=cert_type)
            return

        # TLS enabled and no CA rotation -> Simple certificate rotation
        if tls_state == TLSState.TLS and tls_ca_rotation_state == TLSCARotationState.NO_ROTATION:
            logger.debug(f"Rotating {cert_type.value} certificates")
            return

        # if the cluster is new and the member hasn't started yet, no need to write config or restart just set the tls state
        if not self.charm.state.unit_server.is_started:
            self.charm.tls_manager.set_tls_state(state=TLSState.TLS, tls_type=cert_type)
            return

        # Transition to TLS
        # peer tls needs to be enabled before client tls if both are transitioning (because of peer url broadcasting)
        if (
            cert_type == TLSType.PEER
            and self.charm.state.unit_server.tls_client_state == TLSState.TO_TLS
        ):
            logger.info("Client TLS relation created enable peer TLS and skip restarting")
            return
        elif (
            self.charm.state.unit_server.tls_peer_state == TLSState.TO_TLS
            and not self.charm.state.unit_server.peer_cert_ready
        ):
            logger.info("Peer TLS relation created but cert not ready. defer enabling client TLS")
            event.defer()
            return

        # write config and restart workload
        self.charm.rolling_restart(f"_restart_enable_{cert_type.value}_tls")

    def _on_certificates_broken(self, event: RelationBrokenEvent) -> None:
        """Handle the `certificates-broken` event.

        Args:
            event (RelationBrokenEvent): The event object.
        """
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
            or self.charm.refresh_in_progress
        ):
            logger.warning(
                "Cannot update certificates while cluster is in vulnerable state because of restore, refresh or cluster-rebuild"
            )
            event.defer()
            return

        cert_type = (
            TLSType.PEER if event.relation.name == PEER_TLS_RELATION_NAME else TLSType.CLIENT
        )

        self.charm.tls_manager.set_tls_state(state=TLSState.TO_NO_TLS, tls_type=cert_type)
        self.charm.state.statuses.add(
            TLSStatuses.TLS_DISABLING_PEER_TLS.value
            if cert_type == TLSType.PEER
            else TLSStatuses.TLS_DISABLING_CLIENT_TLS.value,
            scope="unit",
            component=self.charm.tls_manager.name,
        )
        self.charm.tls_manager.set_cert_state(cert_type, is_ready=False)

        if self.charm.state.unit_server.is_started:
            # write config and restart workload
            self.charm.rolling_restart(callback_override=f"_restart_disable_{cert_type.value}_tls")

    def _on_clean_ca(self, event: CleanCAEvent) -> None:
        """Handle the `clean-ca` event.

        Args:
            event (CleanCAEvent): The event object.
        """
        # if all servers have updated the cert, restart the workload to clean up the old CA
        if self.charm.tls_manager.is_cert_updated_on_all_servers(event.cert_type):
            self.charm.rolling_restart("_restart_clean_cas")
        else:
            logger.debug(
                "Waiting for all servers to update certificates before cleaning up old CAs"
            )
            event.defer()

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        """Handle TLS related config changes."""
        if tls_peer_private_key_id := self.charm.config.get(TLS_PEER_PRIVATE_KEY_CONFIG):
            self.update_private_key(tls_peer_private_key_id)

        if tls_client_private_key_id := self.charm.config.get(TLS_CLIENT_PRIVATE_KEY_CONFIG):
            self.update_private_key(tls_client_private_key_id)

        if self.charm.tls_manager.extra_sans_config_is_valid():
            if (
                self.charm.state.unit_server.tls_client_state == TLSState.TLS
                and self.charm.tls_manager.certificate_sans_require_update(TLSType.CLIENT)
                or self.charm.state.unit_server.tls_peer_state == TLSState.TLS
                and self.charm.tls_manager.certificate_sans_require_update(TLSType.PEER)
            ):
                logger.debug("Config change for certificate options, refresh TLS certificates")
                self.refresh_tls_certificates_event.emit()

    def _on_secret_changed(self, event: SecretChangedEvent) -> None:
        """Handle TLS related secret changes."""
        if tls_peer_private_key_id := self.charm.config.get(TLS_PEER_PRIVATE_KEY_CONFIG):
            if tls_peer_private_key_id == event.secret.id:
                self.update_private_key(tls_peer_private_key_id)

        if tls_client_private_key_id := self.charm.config.get(TLS_CLIENT_PRIVATE_KEY_CONFIG):
            if tls_client_private_key_id == event.secret.id:
                self.update_private_key(tls_client_private_key_id)

    def update_private_key(self, private_key_id: str) -> None:
        """Update the private key in etcd."""
        logger.debug("Updating TLS private key.")

        if self.charm.tls_manager.read_and_validate_private_key(private_key_id) is None:
            logger.error("Invalid private key provided, cannot update TLS certificates.")
            return

        self.refresh_tls_certificates_event.emit()
