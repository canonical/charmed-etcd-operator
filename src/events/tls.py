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
from ops import EventSource, Handle, RelationBrokenEvent, RelationCreatedEvent
from ops.framework import EventBase, Object

from literals import (
    CLIENT_TLS_RELATION_NAME,
    PEER_TLS_RELATION_NAME,
    Status,
    TLSCARotationState,
    TLSState,
    TLSType,
)

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

    def snapshot(self) -> dict:
        """Snapshot of lock event."""
        return {"cert_type": self.cert_type.value}

    def restore(self, snapshot: dict):
        """Restores lock event."""
        self.cert_type = TLSType(snapshot["cert_type"])


class TLSEvents(Object):
    """Event handlers for related applications on the `certificates` relation interface."""

    clean_ca_event = EventSource(CleanCAEvent)

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, "tls")
        self.charm: "EtcdOperatorCharm" = charm
        host_mapping = self.charm.cluster_manager.get_host_mapping()
        common_name = f"{self.charm.unit.name}-{self.charm.model.uuid}"
        self.peer_certificate = TLSCertificatesRequiresV4(
            self.charm,
            PEER_TLS_RELATION_NAME,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=common_name,
                    sans_ip=frozenset({host_mapping["ip"]}),
                    sans_dns=frozenset({self.charm.unit.name, host_mapping["hostname"]}),
                    organization=TLSType.PEER.value,
                ),
            ],
        )
        self.client_certificate = TLSCertificatesRequiresV4(
            self.charm,
            CLIENT_TLS_RELATION_NAME,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=common_name,
                    sans_ip=frozenset({host_mapping["ip"]}),
                    sans_dns=frozenset({self.charm.unit.name, host_mapping["hostname"]}),
                    organization=TLSType.CLIENT.value,
                ),
            ],
        )

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

    def _on_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle the `relation-created` event.

        Args:
            event (RelationCreatedEvent): The event object.
        """
        if event.relation.name == PEER_TLS_RELATION_NAME:
            self.charm.tls_manager.set_tls_state(state=TLSState.TO_TLS, tls_type=TLSType.PEER)
            self.charm.set_status(Status.TLS_ENABLING_PEER_TLS)
        else:
            self.charm.tls_manager.set_tls_state(state=TLSState.TO_TLS, tls_type=TLSType.CLIENT)
            self.charm.set_status(Status.TLS_ENABLING_CLIENT_TLS)

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Handle the `certificates-available` event.

        Args:
            event (CertificateAvailableEvent): The event object.
        """
        cert = event.certificate
        cert_type = TLSType(cert.organization)
        logger.debug(f"Received certificate for {cert_type}")

        relation_requirer = (
            self.peer_certificate if cert_type == TLSType.PEER else self.client_certificate
        )

        certs, private_key = relation_requirer.get_assigned_certificates()
        cert = certs[0]

        tls_state = (
            self.charm.state.unit_server.tls_peer_state
            if cert_type == TLSType.PEER
            else self.charm.state.unit_server.tls_client_state
        )
        tls_ca_rotation_state = (
            self.charm.state.unit_server.tls_peer_ca_rotation_state
            if cert_type == TLSType.PEER
            else self.charm.state.unit_server.tls_client_ca_rotation_state
        )

        if (
            tls_state == TLSState.TLS
            and self.charm.tls_manager.is_new_ca(cert.ca.raw, cert_type)
            and tls_ca_rotation_state == TLSCARotationState.NO_ROTATION
        ):
            logger.debug(f"New {cert_type} CA detected, updating trusted CAs")
            self.charm.tls_manager.add_trusted_ca(cert.ca.raw, cert_type)
            self.charm.tls_manager.set_ca_rotation_state(
                cert_type, TLSCARotationState.NEW_CA_DETECTED
            )
            self.charm.rolling_restart("_restart_ca_rotation")
            event.defer()
            return

        # writing certificate after CA rotation
        if tls_ca_rotation_state == TLSCARotationState.NEW_CA_ADDED:
            if not self.charm.tls_manager.is_new_ca_saved_on_all_servers(cert_type):
                logger.debug("Waiting for all servers to update CA")
                event.defer()
                return

        # write certificates to disk
        self.charm.tls_manager.write_certificate(cert, private_key)  # type: ignore

        # TLS is enabled, New CA added to all servers, and cert updated -> no rolling restart needed
        if tls_state == TLSState.TLS and tls_ca_rotation_state == TLSCARotationState.NEW_CA_ADDED:
            logger.debug(f"Updating {cert_type.value} certificates with new CA")
            self.charm.tls_manager.set_ca_rotation_state(
                cert_type, TLSCARotationState.CERT_UPDATED
            )
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
        cert_type = (
            TLSType.PEER if event.relation.name == PEER_TLS_RELATION_NAME else TLSType.CLIENT
        )

        self.charm.tls_manager.set_tls_state(state=TLSState.TO_NO_TLS, tls_type=cert_type)
        self.charm.set_status(
            Status.TLS_DISABLING_PEER_TLS
            if cert_type == TLSType.PEER
            else Status.TLS_DISABLING_CLIENT_TLS
        )
        self.charm.tls_manager.set_cert_state(cert_type, is_ready=False)

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
