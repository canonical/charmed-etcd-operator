#!/usr/bin/env python3
# Copyright 2024 Canonical Limited
# See LICENSE file for licensing details.

"""TLS related event handlers."""

import base64
import logging
import re
from typing import TYPE_CHECKING

from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    PrivateKey,
    TLSCertificatesRequiresV4,
)
from ops import (
    EventSource,
    Handle,
    ModelError,
    RelationBrokenEvent,
    RelationCreatedEvent,
    SecretNotFoundError,
)
from ops.framework import EventBase, Object

from common.secrets import get_secret_from_id
from literals import (
    CLIENT_TLS_RELATION_NAME,
    PEER_TLS_RELATION_NAME,
    TLS_CLIENT_PRIVATE_KEY_CONFIG,
    TLS_PEER_PRIVATE_KEY_CONFIG,
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

    def snapshot(self) -> dict[str, str]:
        """Snapshot of lock event."""
        return {"cert_type": self.cert_type.value}

    def restore(self, snapshot: dict[str, str]) -> None:
        """Restores lock event."""
        self.cert_type = TLSType(snapshot["cert_type"])


class RefreshTLSCertificatesEvent(EventBase):
    """Event for refreshing peer TLS certificates."""

    def __init__(self, handle: Handle):
        super().__init__(handle)

    def snapshot(self) -> dict[str, str]:
        """Snapshot of lock event."""
        return {}

    def restore(self, snapshot: dict[str, str]) -> None:
        """Restores lock event."""
        pass


class TLSEvents(Object):
    """Event handlers for related applications on the `certificates` relation interface."""

    clean_ca_event = EventSource(CleanCAEvent)
    refresh_tls_certificates_event = EventSource(RefreshTLSCertificatesEvent)

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, "tls")
        self.charm: "EtcdOperatorCharm" = charm
        host_mapping = self.charm.cluster_manager.get_host_mapping()
        common_name = f"{self.charm.unit.name}-{self.charm.model.uuid}"
        peer_private_key = None
        client_private_key = None

        if peer_private_key_id := self.charm.config.get(TLS_PEER_PRIVATE_KEY_CONFIG):
            if (
                peer_private_key := self._read_and_validate_private_key(peer_private_key_id)
            ) is None:
                self.charm.set_status(Status.TLS_INVALID_PRIVATE_KEY)

        if client_private_key_id := self.charm.config.get(TLS_CLIENT_PRIVATE_KEY_CONFIG):
            if (
                client_private_key := self._read_and_validate_private_key(client_private_key_id)
            ) is None:
                self.charm.set_status(Status.TLS_INVALID_PRIVATE_KEY)

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
            private_key=peer_private_key,
            refresh_events=[self.refresh_tls_certificates_event],
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
        if tls_ca_rotation_state in [
            TLSCARotationState.NEW_CA_DETECTED,
            TLSCARotationState.NEW_CA_ADDED,
        ]:
            if not self.charm.tls_manager.is_new_ca_saved_on_all_servers(cert_type):
                logger.debug("Waiting for all servers to update CA")
                event.defer()
                return

        # write certificates to disk
        self.charm.tls_manager.write_certificate(cert, private_key)  # type: ignore

        # TLS is enabled, New CA added to all servers, and cert updated -> no rolling restart needed until we clean up old CA
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

    def _read_and_validate_private_key(
        self, private_key_secret_id: str | None
    ) -> PrivateKey | None:
        """Read and validate the private key.

        Args:
            private_key_secret_id (str): The private key secret ID.

        Returns:
            PrivateKey: The private key.
        """
        try:
            secret_content = get_secret_from_id(self.charm.model, private_key_secret_id).get(
                "private-key"
            )
        except (ModelError, SecretNotFoundError) as e:
            logger.error(e)
            return None

        if secret_content is None:
            logger.error(f"Secret {private_key_secret_id} does not contain a private key.")
            return None

        private_key = (
            secret_content
            if re.match(r"(-+(BEGIN|END) [A-Z ]+-+)", secret_content)
            else base64.b64decode(secret_content).decode("utf-8").strip()
        )
        private_key = PrivateKey(raw=private_key)
        if not private_key.is_valid():
            logger.error("Invalid private key format.")
            return None

        return private_key
