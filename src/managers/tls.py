#!/usr/bin/env python3
# Copyright 2024 Canonical Limited
# See LICENSE file for licensing details.

"""Manager for handling TLS related events."""

import logging
from enum import Enum
from pathlib import Path

from charms.tls_certificates_interface.v4.tls_certificates import (
    PrivateKey,
    ProviderCertificate,
)

from core.cluster import ClusterState
from core.models import TLSState
from core.workload import WorkloadBase
from literals import SUBSTRATES

logger = logging.getLogger(__name__)


class CertType(Enum):
    """Certificate types."""

    PEER = "peer"
    CLIENT = "client"


class TLSManager:
    """Manage all TLS related events."""

    def __init__(self, state: ClusterState, workload: WorkloadBase, substrate: SUBSTRATES):
        self.state = state
        self.workload = workload
        self.substrate = substrate

    def set_tls_state(self, state: TLSState):
        """Set the TLS state.

        Args:
            state (TLSState): The TLS state.
        """
        logger.debug(f"Setting TLS state to {state}")
        self.state.unit_server.update(
            {
                "tls-state": state.value,
            }
        )

    def write_certificate(self, certificate: ProviderCertificate, private_key: PrivateKey):
        """Write certificates to disk.

        Args:
            certificate (ProviderCertificate): The certificate.
            private_key (PrivateKey): The private key.
        """
        logger.debug("Writing certificates to disk")
        ca_cert = certificate.ca
        cert_type = CertType(certificate.certificate.organization)
        match cert_type:
            case CertType.PEER:
                certificate_path = Path(self.workload.paths.tls.peer_cert)
                self.add_trusted_ca(ca_cert.raw, client=False)
            case CertType.CLIENT:
                certificate_path = Path(self.workload.paths.tls.client_cert)
                self.add_trusted_ca(ca_cert.raw, client=True)
        private_key_path = certificate_path.with_suffix(".key")
        self.workload.write_file(private_key.raw, private_key_path.as_posix())
        self.workload.write_file(certificate.certificate.raw, certificate_path.as_posix())
        self.set_cert_status(cert_type, is_ready=True)

    def add_trusted_ca(self, ca_cert: str, client: bool = False):
        """Add trusted CA to the system.

        Args:
            ca_cert (str): The CA certificate.
            client (bool): Add the client CA. Defaults to False.
        """
        ca_certs_path = Path(self.workload.paths.tls.peer_ca)
        if client:
            ca_certs_path = Path(self.workload.paths.tls.client_ca)

        cas = self._load_trusted_ca(client=client)
        if ca_cert not in cas:
            cas.append(ca_cert)
            self.workload.write_file("\n".join(cas), ca_certs_path.as_posix())

    def _load_trusted_ca(self, client: bool = False):
        """Load trusted CA from the system.

        Args:
            client (bool): Load the client CA. Defaults to False.
        """
        ca_certs_path = Path(self.workload.paths.tls.peer_ca)
        if client:
            ca_certs_path = Path(self.workload.paths.tls.client_ca)

        if not ca_certs_path.exists():
            return []

        # split the certificates by the end of the certificate marker and keep the marker in the cert
        raw_cas = ca_certs_path.read_text().split("-----END CERTIFICATE-----")
        # add the marker back to the certificate
        return [cert.strip() + "\n-----END CERTIFICATE-----" for cert in raw_cas if cert.strip()]

    def set_cert_status(self, cert_type: CertType, is_ready: bool):
        """Set the certificate status.

        Args:
            cert_type (CertType): The certificate type.
            is_ready (bool): The certificate status.
        """
        self.state.unit_server.update({f"{cert_type.value}-cert-ready": str(is_ready)})

    @property
    def certs_ready(self) -> bool:
        """Check if all certificates are ready."""
        return self.state.unit_server.peer_cert_ready and self.state.unit_server.client_cert_ready

    def delete_certificates(self):
        """Delete the certificate, key and its CA from disk."""
        for cert_type in CertType:
            logger.debug(f"Deleting {cert_type} certificate")
            cert_path = Path(self.workload.paths.tls.peer_cert)
            key_path = Path(self.workload.paths.tls.peer_key)
            ca_path = Path(self.workload.paths.tls.peer_ca)

            if cert_type == CertType.CLIENT:
                cert_path = Path(self.workload.paths.tls.client_cert)

            if cert_path.exists():
                cert_path.unlink()

            if key_path.exists():
                key_path.unlink()

            if ca_path.exists():
                ca_path.unlink()
            logger.debug(f"Deleted {cert_type} certificate")
