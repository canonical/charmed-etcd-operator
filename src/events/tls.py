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
from ops import RelationBrokenEvent, RelationCreatedEvent
from ops.framework import Object

from literals import CLIENT_TLS_RELATION_NAME, PEER_TLS_RELATION_NAME, Status, TLSState, TLSType

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class TLSEvents(Object):
    """Event handlers for related applications on the `certificates` relation interface."""

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

        # write certificates to disk
        self.charm.tls_manager.write_certificate(cert, private_key)  # type: ignore

        # Rotating certificates
        if (
            cert_type == TLSType.PEER
            and self.charm.state.unit_server.tls_peer_state == TLSState.TLS
        ):
            logger.debug("Rotating peer certificates")
            return

        if (
            cert_type == TLSType.CLIENT
            and self.charm.state.unit_server.tls_client_state == TLSState.TLS
        ):
            logger.debug("Rotating client certificates")
            return

        # if the cluster is new and the member hasn't started yet, no need to write config or restart just set the tls state
        if not self.charm.state.unit_server.is_started:
            self.charm.tls_manager.set_tls_state(state=TLSState.TLS, tls_type=cert_type)
            return

        # peer tls needs to be enabled before client tls if both are transitioning (because of peer url broadcasting)
        logger.debug(
            f"client state: {self.charm.state.unit_server.tls_client_state} ; peer state: {self.charm.state.unit_server.tls_peer_state}"
        )
        logger.debug(
            f"client cert ready: {self.charm.state.unit_server.client_cert_ready} ; peer cert ready: {self.charm.state.unit_server.peer_cert_ready}"
        )
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

        if self.charm.state.unit_server.is_started:
            # write config and restart workload
            self.charm.rolling_restart(callback_override=f"_restart_disable_{cert_type.value}_tls")
