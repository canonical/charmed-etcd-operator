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

from core.models import TLSState
from literals import CLIENT_TLS_RELATION_NAME, PEER_TLS_RELATION_NAME, Status
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
        self.peer_certificate = TLSCertificatesRequiresV4(
            self.charm,
            PEER_TLS_RELATION_NAME,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=self.charm.state.unit_server.common_name,
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
                    common_name=self.charm.state.unit_server.common_name,
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

        self.framework.observe(
            self.charm.on[PEER_TLS_RELATION_NAME].relation_created, self._on_relation_created
        )

        self.framework.observe(
            self.charm.on[CLIENT_TLS_RELATION_NAME].relation_created, self._on_relation_created
        )

        self.framework.observe(
            self.charm.on[PEER_TLS_RELATION_NAME].relation_joined, self._on_relation_joined
        )

        self.framework.observe(
            self.charm.on[CLIENT_TLS_RELATION_NAME].relation_joined, self._on_relation_joined
        )

        self.framework.observe(
            self.charm.on[PEER_TLS_RELATION_NAME].relation_broken, self._on_certificates_broken
        )
        self.framework.observe(
            self.charm.on[CLIENT_TLS_RELATION_NAME].relation_broken, self._on_certificates_broken
        )

    def _on_relation_created(self, event: RelationCreatedEvent):
        """Handle the `relation-created` event."""
        self.charm.tls_manager.set_tls_state(state=TLSState.TO_TLS)

    def _on_relation_joined(self, event: RelationJoinedEvent):
        """Handle the `relation-joined` event."""
        if event.relation.name == PEER_TLS_RELATION_NAME:
            if not self.charm.state.client_tls_relation:
                self.charm.set_status(Status.CLIENT_TLS_MISSING)
                event.defer()
        elif event.relation.name == CLIENT_TLS_RELATION_NAME:
            if not self.charm.state.peer_tls_relation:
                self.charm.set_status(Status.PEER_TLS_MISSING)
                event.defer()

    def _on_certificate_available(self, event: CertificateAvailableEvent):
        """Handle the `certificates-available` event."""
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
            raise Exception("Missing certificate or private key")

        self.charm.set_status(Status.TLS_NOT_READY)
        # write certificates to disk
        self.charm.tls_manager.write_certificate(cert, private_key)

        if self.charm.tls_manager.certs_ready:
            if self.charm.workload.alive():
                # broadcast change
                self.charm.cluster_manager.broadcast_peer_url(
                    self.charm.state.unit_server.peer_url.replace("http://", "https://")
                )
            # write configuration file
            self.charm.config_manager.set_config_properties()
            self.charm.tls_manager.set_tls_state(state=TLSState.TLS)

            # restart the workload if it is running
            if self.charm.workload.alive():
                self.charm.workload.restart()
                if self.charm.cluster_manager.health_check():
                    self.charm.set_status(Status.ACTIVE)
                else:
                    self.charm.set_status(Status.HEALTH_CHECK_FAILED)
            # self.charm.set_status(Status.ACTIVE)
        else:
            logger.debug("A certificate is missing, waiting for the next certificate event.")
            if self.charm.state.client_tls_relation is None:
                self.charm.set_status(Status.CLIENT_TLS_MISSING)
            if self.charm.state.peer_tls_relation is None:
                logger.debug("setting status to peer tls missing")
                self.charm.set_status(Status.PEER_TLS_MISSING)

    def _on_certificates_broken(self, event: RelationBrokenEvent):
        """Handle the `certificates-broken` event."""
        cert_type = (
            CertType.PEER if event.relation.name == PEER_TLS_RELATION_NAME else CertType.CLIENT
        )

        self.charm.tls_manager.set_tls_state(state=TLSState.TO_NO_TLS)
        self.charm.set_status(Status.TLS_NOT_READY)
        self.charm.tls_manager.set_cert_status(cert_type, is_ready=False)

        if cert_type == CertType.PEER and self.charm.state.client_tls_relation:
            self.charm.set_status(Status.CLIENT_TLS_NEEDS_TO_BE_REMOVED)

        if cert_type == CertType.CLIENT and self.charm.state.peer_tls_relation:
            self.charm.set_status(Status.PEER_TLS_NEEDS_TO_BE_REMOVED)

        # write config and restart workload
        if (
            not self.charm.state.unit_server.peer_cert_ready
            and not self.charm.state.unit_server.client_cert_ready
        ):
            # broadcast new peer url
            logger.debug("Broadcasting new peer url")
            self.charm.cluster_manager.broadcast_peer_url(
                self.charm.state.unit_server.peer_url.replace("https://", "http://")
            )
            # delete certificates from disk
            self.charm.tls_manager.delete_certificates()
            logger.debug("Writing config properties")
            self.charm.config_manager.set_config_properties()
            logger.debug("Restarting workload")
            self.charm.workload.restart()
            logger.debug("Checking the health of the cluster")
            self.charm.tls_manager.set_tls_state(state=TLSState.NO_TLS)
            if self.charm.cluster_manager.health_check(cluster=False):
                logger.debug("Setting status to active")
                self.charm.set_status(Status.ACTIVE)
            else:
                self.charm.set_status(Status.HEALTH_CHECK_FAILED)
                event.defer()
