#!/usr/bin/env python3
# Copyright 2024 Canonical Limited
# See LICENSE file for licensing details.

"""Manager for handling TLS related events."""

import logging
from pathlib import Path

from charms.tls_certificates_interface.v4.tls_certificates import (
    PrivateKey,
    ProviderCertificate,
)

from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import SUBSTRATES, TLSCARotationState, TLSState, TLSType

logger = logging.getLogger(__name__)


class TLSManager:
    """Manage all TLS related events."""

    def __init__(self, state: ClusterState, workload: WorkloadBase, substrate: SUBSTRATES):
        self.state = state
        self.workload = workload
        self.substrate = substrate

    def set_tls_state(self, state: TLSState, tls_type: TLSType) -> None:
        """Set the TLS state.

        Args:
            state (TLSState): The TLS state.
            tls_type (TLSType): The tls type type.
        """
        logger.debug(f"Setting {tls_type.value} TLS state to {state}")
        self.state.unit_server.update(
            {
                f"tls_{tls_type.value}_state": state.value,
            }
        )

    def write_certificate(self, certificate: ProviderCertificate, private_key: PrivateKey) -> None:
        """Write certificates to disk.

        Args:
            certificate (ProviderCertificate): The certificate.
            private_key (PrivateKey): The private key.
        """
        logger.debug("Writing certificates to disk")
        ca_cert = certificate.ca
        cert_type = TLSType(certificate.certificate.organization)
        if cert_type == TLSType.CLIENT:
            certificate_path = self.workload.paths.tls.client_cert
            private_key_path = self.workload.paths.tls.client_key
        else:
            certificate_path = self.workload.paths.tls.peer_cert
            private_key_path = self.workload.paths.tls.peer_key

        self.add_trusted_ca(ca_cert.raw, cert_type)
        self.workload.write_file(private_key.raw, private_key_path)
        self.workload.write_file(certificate.certificate.raw, certificate_path)
        self.set_cert_state(cert_type, is_ready=True)

    def is_new_ca(self, ca_cert: str, tls_type: TLSType) -> bool:
        """Check if the CA is new.

        Args:
            ca_cert (str): The CA certificate.
            tls_type (TLSType): The TLS type.

        Returns:
            bool: True if the CA is new, False otherwise.
        """
        cas = self._load_trusted_ca(tls_type)
        return ca_cert not in cas

    def add_trusted_ca(self, ca_cert: str, tls_type: TLSType = TLSType.PEER) -> None:
        """Add trusted CA to the system.

        Args:
            ca_cert (str): The CA certificate.
            tls_type (TLSType): The TLS type. Defaults to TLSType.PEER.
        """
        if tls_type == TLSType.CLIENT:
            ca_certs_path = self.workload.paths.tls.client_ca
        else:
            ca_certs_path = self.workload.paths.tls.peer_ca

        cas = self._load_trusted_ca(tls_type)
        if ca_cert not in cas:
            cas.append(ca_cert)
            self.workload.write_file("\n".join(cas), ca_certs_path)

    def _load_trusted_ca(self, tls_type) -> list[str]:
        """Load trusted CA from the system.

        Args:
            tls_type (TLSType): The TLS type. Defaults to TLSType.PEER.
        """
        if tls_type == TLSType.CLIENT:
            ca_certs_path = Path(self.workload.paths.tls.client_ca)
        else:
            ca_certs_path = Path(self.workload.paths.tls.peer_ca)

        if not ca_certs_path.exists():
            return []

        # split the certificates by the end of the certificate marker and keep the marker in the cert
        raw_cas = ca_certs_path.read_text().split("-----END CERTIFICATE-----")
        # add the marker back to the certificate
        return [cert.strip() + "\n-----END CERTIFICATE-----" for cert in raw_cas if cert.strip()]

    def set_cert_state(self, cert_type: TLSType, is_ready: bool) -> None:
        """Set the certificate state.

        Args:
            cert_type (TLSType): The certificate type.
            is_ready (bool): The certificate state.
        """
        self.state.unit_server.update({f"{cert_type.value}_cert_ready": str(is_ready)})

    def delete_certificates(self, cert_type: TLSType) -> None:
        """Delete the certificate, key and its CA from disk."""
        logger.debug(f"Deleting {cert_type.value} certificates")
        if cert_type == TLSType.CLIENT:
            self.workload.remove_file(self.workload.paths.tls.client_cert)
            self.workload.remove_file(self.workload.paths.tls.client_ca)
            self.workload.remove_file(self.workload.paths.tls.client_key)
        else:
            self.workload.remove_file(self.workload.paths.tls.peer_cert)
            self.workload.remove_file(self.workload.paths.tls.peer_ca)
            self.workload.remove_file(self.workload.paths.tls.peer_key)
        logger.debug(f"Deleted {cert_type.value} certificate")

    def clean_cas(self, tls_type: TLSType) -> None:
        """Clean the CAs from the system.

        Args:
            tls_type (TLSType): The TLS type.
        """
        cas = self._load_trusted_ca(tls_type)
        # clear cas file
        cas_path = (
            self.workload.paths.tls.client_ca
            if tls_type == TLSType.CLIENT
            else self.workload.paths.tls.peer_ca
        )
        self.workload.remove_file(cas_path)
        # The last CA is the new CA, so we add it back
        # We will have at most 2 CAs in the list, the old and the new one
        # These CAs are for certificates used by the server only so no need
        # for multiple active CAs
        self.add_trusted_ca(cas[-1], tls_type)

    def set_ca_rotation_state(self, tls_type: TLSType, state: TLSCARotationState) -> None:
        """Set the CA rotation state.

        Args:
            tls_type (TLSType): The TLS type.
            state (TLSCARotationState): The CA rotation state.
        """
        logger.debug(f"Setting {tls_type.value} CA rotation state to {state}")
        self.state.unit_server.update({f"tls_{tls_type.value}_ca_rotation": str(state.value)})

    def is_new_ca_saved_on_all_servers(self, cert_type: TLSType) -> bool:
        """Check if the new CA is saved on all servers.

        Args:
            cert_type (TLSType): The certificate type.
        """
        for server in self.state.servers:
            server_ca_rotation_state = (
                server.tls_peer_ca_rotation_state
                if cert_type == TLSType.PEER
                else server.tls_client_ca_rotation_state
            )
            if server_ca_rotation_state in [
                TLSCARotationState.NO_ROTATION,
                TLSCARotationState.NEW_CA_DETECTED,
            ]:
                return False
        return True

    def is_cert_updated_on_all_servers(self, cert_type: TLSType) -> bool:
        """Check if the certificate is updated on all servers.

        Args:
            cert_type (TLSType): The certificate type.
        """
        for server in self.state.servers:
            server_ca_state = (
                server.tls_peer_ca_rotation_state
                if cert_type == TLSType.PEER
                else server.tls_client_ca_rotation_state
            )
            if server_ca_state in [
                TLSCARotationState.NEW_CA_DETECTED,
                TLSCARotationState.NEW_CA_ADDED,
            ]:
                return False
        return True
