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
from ops import RelationBrokenEvent, RelationCreatedEvent, RelationJoinedEvent
from ops.framework import Object

from common.exceptions import TLSMissingCertificateOrKeyError
from literals import CLIENT_TLS_RELATION_NAME, PEER_TLS_RELATION_NAME, Status, TLSState
from managers.tls import CertType

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
                    organization=CertType.PEER.value,
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
                    organization=CertType.CLIENT.value,
                ),
            ],
        )

        self.framework.observe(
            self.peer_certificate.on.certificate_available, self._on_certificate_available
        )

        self.framework.observe(
            self.client_certificate.on.certificate_available, self._on_certificate_available
        )

        for relation in [PEER_TLS_RELATION_NAME, CLIENT_TLS_RELATION_NAME]:
            self.framework.observe(
                self.charm.on[relation].relation_created, self._on_relation_created
            )
            self.framework.observe(
                self.charm.on[relation].relation_joined, self._on_relation_joined
            )
            self.framework.observe(
                self.charm.on[relation].relation_broken, self._on_certificates_broken
            )

    def _on_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle the `relation-created` event.

        Args:
            event (RelationCreatedEvent): The event object.
        """
        self.charm.tls_manager.set_tls_state(state=TLSState.TO_TLS)
        self.charm.set_status(Status.TLS_NOT_READY)

    def _on_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle the `relation-joined` event.

        Args:
            event (RelationJoinedEvent): The event object.
        """
        if event.relation.name == PEER_TLS_RELATION_NAME:
            if not self.charm.state.client_tls_relation:
                self.charm.set_status(Status.TLS_CLIENT_TLS_MISSING)
                event.defer()
        elif event.relation.name == CLIENT_TLS_RELATION_NAME:
            if not self.charm.state.peer_tls_relation:
                self.charm.set_status(Status.TLS_PEER_TLS_MISSING)
                event.defer()

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Handle the `certificates-available` event.

        Args:
            event (CertificateAvailableEvent): The event object.
        """
        cert = event.certificate
        cert_type = CertType(cert.organization)
        logger.debug(f"Received certificate for {cert_type}")

        relation_requirer = (
            self.peer_certificate if cert_type == CertType.PEER else self.client_certificate
        )

        certs, private_key = relation_requirer.get_assigned_certificates()
        cert = certs[0] if certs else None

        if not cert or not private_key:
            logger.error("Missing certificate or private key")
            raise TLSMissingCertificateOrKeyError("Missing certificate or private key")

        # write certificates to disk
        self.charm.tls_manager.write_certificate(cert, private_key)

        if self.charm.state.unit_server.certs_ready:
            # we do not restart if the cluster has not started yet
            if self.charm.state.cluster.initial_cluster_state == "existing":
                self.charm.rolling_restart()
            else:
                self.charm.tls_manager.set_tls_state(state=TLSState.TLS)
        else:
            logger.debug("A certificate is missing, waiting for the next certificate event.")
            if self.charm.state.client_tls_relation is None:
                self.charm.set_status(Status.TLS_CLIENT_TLS_MISSING)
            if self.charm.state.peer_tls_relation is None:
                logger.debug("setting status to peer tls missing")
                self.charm.set_status(Status.TLS_PEER_TLS_MISSING)

    def _on_certificates_broken(self, event: RelationBrokenEvent) -> None:
        """Handle the `certificates-broken` event.

        Args:
            event (RelationBrokenEvent): The event object.
        """
        cert_type = (
            CertType.PEER if event.relation.name == PEER_TLS_RELATION_NAME else CertType.CLIENT
        )

        self.charm.tls_manager.set_tls_state(state=TLSState.TO_NO_TLS)
        self.charm.set_status(Status.TLS_DISABLING)
        self.charm.tls_manager.set_cert_state(cert_type, is_ready=False)

        if cert_type == CertType.PEER and self.charm.state.client_tls_relation:
            self.charm.set_status(Status.TLS_CLIENT_TLS_NEEDS_TO_BE_REMOVED)

        if cert_type == CertType.CLIENT and self.charm.state.peer_tls_relation:
            self.charm.set_status(Status.TLS_PEER_TLS_NEEDS_TO_BE_REMOVED)

        # write config and restart workload
        if (
            not self.charm.state.unit_server.peer_cert_ready
            and not self.charm.state.unit_server.client_cert_ready
        ):
            self.charm.rolling_restart()
