#!/usr/bin/env python3
# Copyright 2024 Canonical Limited
# See LICENSE file for licensing details.

"""Manager for handling TLS related events."""

import logging
import re
import socket
from ipaddress import ip_address
from pathlib import Path
from typing import Dict, Iterable

from charms.tls_certificates_interface.v4.tls_certificates import (
    PrivateKey,
    ProviderCertificate,
)

from common.certificates import is_leaf_certificate_valid, leaf_certificate
from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import SUBSTRATES, Status, TLSCARotationState, TLSState, TLSType

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

    def write_certificate(
        self, certificate: ProviderCertificate, private_key: PrivateKey, cert_type: TLSType
    ) -> None:
        """Write certificates to disk.

        Args:
            certificate (ProviderCertificate): The certificate.
            private_key (PrivateKey): The private key.
            cert_type (TLSType): The certificate type (client or peer).
        """
        logger.debug("Writing certificates to disk")
        ca_cert = certificate.ca
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

    def is_new_ca(self, certificate: str, tls_type: TLSType) -> bool:
        """Check if the certificate is a new CA.

        Args:
            certificate (str): The certificate to check.
            tls_type (TLSType): The TLS type.

        Returns:
            bool: True if the certificate is not stored in the client trusted CA store, False otherwise.
        """
        trusted_cas = self.load_trusted_ca(tls_type)
        return certificate not in trusted_cas

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

        cas = self.load_trusted_ca(tls_type)
        if ca_cert not in cas:
            cas.add(ca_cert)
            self.workload.write_file("\n".join(cas), ca_certs_path)

    def load_trusted_ca(self, tls_type: TLSType) -> set[str]:
        """Load trusted CA from the system.

        Args:
            tls_type (TLSType): The TLS type. Defaults to TLSType.PEER.
        """
        if tls_type == TLSType.CLIENT:
            ca_certs_path = Path(self.workload.paths.tls.client_ca)
        else:
            ca_certs_path = Path(self.workload.paths.tls.peer_ca)

        if not ca_certs_path.exists():
            return set()

        return self.separate_certificates(ca_certs_path.read_text())

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

    def separate_certificates(self, concatenated_certs: str) -> set[str]:
        """Separate certificates from the concatenated certificates.

        Args:
            concatenated_certs (str): The concatenated certificates.

        Returns:
            set[str]: The set of certificates.
        """
        # split the certificates by the end of the certificate marker and keep the marker in the cert
        raw_cas = concatenated_certs.split("-----END CERTIFICATE-----")
        # add the marker back to the certificate
        return {cert.strip() + "\n-----END CERTIFICATE-----" for cert in raw_cas if cert.strip()}

    def update_cas(self, cas: Iterable[str], tls_type: TLSType) -> None:
        """Update the CAs.

        Args:
            cas (Iterable[str]): The list/set of CAs.
            tls_type (TLSType): The TLS type.
        """
        self.workload.write_file(
            "\n".join(cas),
            self.workload.paths.tls.peer_ca
            if tls_type == TLSType.PEER
            else self.workload.paths.tls.client_ca,
        )

    def check_certificate_validity(self, tls_type: TLSType) -> None:
        """Check if the certificates installed on the unit will soon expire.

        Args:
            tls_type (TLSType): The TLS type to check certificate validity for.
        """
        cert_files = []
        if tls_type == TLSType.CLIENT and self.state.unit_server.tls_client_state == TLSState.TLS:
            cert_files.append(self.workload.paths.tls.client_cert)
            cert_files.append(self.workload.paths.tls.client_ca)
        elif tls_type == TLSType.PEER and self.state.unit_server.tls_peer_state == TLSState.TLS:
            cert_files.append(self.workload.paths.tls.peer_cert)
            cert_files.append(self.workload.paths.tls.peer_ca)

        for cert_file in cert_files:
            # will raise CalledProcessError if cert expires in less than 24h (=86400s)
            self.workload.exec(
                [
                    "openssl",
                    "x509",
                    "-checkend",
                    "86400",
                    "-noout",
                    "-in",
                    cert_file,
                ]
            )

    def compute_component_status(self) -> list[Status]:
        """Compute the component status."""
        status_list = []

        if self.state.unit_server.tls_peer_state == TLSState.TO_TLS:
            status_list.append(Status.TLS_ENABLING_PEER_TLS)

        if self.state.unit_server.tls_client_state == TLSState.TO_TLS:
            status_list.append(Status.TLS_ENABLING_CLIENT_TLS)

        if self.state.unit_server.tls_peer_state == TLSState.TO_NO_TLS:
            status_list.append(Status.TLS_DISABLING_PEER_TLS)

        if self.state.unit_server.tls_client_state == TLSState.TO_NO_TLS:
            status_list.append(Status.TLS_DISABLING_CLIENT_TLS)

        if self.state.unit_server.tls_peer_ca_rotation_state != TLSCARotationState.NO_ROTATION:
            status_list.append(Status.TLS_PEER_CA_ROTATING)

        if self.state.unit_server.tls_client_ca_rotation_state != TLSCARotationState.NO_ROTATION:
            status_list.append(Status.TLS_CLIENT_CA_ROTATING)

        if self.state.unit_server.tls_client_certs_expiring:
            status_list.append(Status.TLS_CLIENT_CERTS_EXPIRING)

        if self.state.unit_server.tls_peer_certs_expiring:
            status_list.append(Status.TLS_PEER_CERTS_EXPIRING)

        if not self.extra_sans_config_is_valid():
            status_list.append(Status.SANS_CONFIG_INVALID)

        return status_list

    def build_sans_ip(self, tls_type: TLSType) -> frozenset[str]:
        """Build the SANs IP for the TLS certificate.

        Returns:
            frozenset[str]: The SANs IP.
        """
        sans_ip = set()
        if self.extra_sans_config_is_valid() and (
            extra_sans_config := self.state.config.get("certificate-extra-sans")
        ):
            extra_sans = [san.strip() for san in extra_sans_config.split(",")]
            sans_ip = {san for san in extra_sans if len(san.split(".")) == 4}

        if private_ip := self.workload.get_private_ip():
            sans_ip.add(private_ip)
        else:
            logger.warning("No private IP found using unit-get. Using socket instead.")
            sans_ip.add(socket.gethostbyname(socket.gethostname()))

        if tls_type == TLSType.PEER:
            logger.debug(f"Using private IP {private_ip} for peer SANs IP.")
            return frozenset(sans_ip)

        # For client TLS, we use both private and public IPs if available
        if public_ip := self.workload.get_public_ip():
            logger.debug("Using public and private IPs for SANs.")
            sans_ip.add(public_ip)

        return frozenset(sans_ip)

    def build_sans_dns(self) -> frozenset[str]:
        """Build the SANs DNS for the TLS certificate.

        Returns:
            frozenset[str]: The SANs DNS.
        """
        sans_dns = set()
        if self.extra_sans_config_is_valid() and (
            extra_sans_config := self.state.config.get("certificate-extra-sans")
        ):
            extra_sans = [san.strip() for san in extra_sans_config.split(",")]
            sans_dns = {
                san.replace("{unit}", str(self.state.unit_server.unit_id))
                for san in extra_sans
                if len(san.split(".")) != 4
            }

        sans_dns.add(self.state.unit_server.unit_name)
        sans_dns.add(self.workload.get_host_mapping()["hostname"])
        return frozenset(sans_dns)

    def extra_sans_config_is_valid(self) -> bool:
        """Validate configuration value for certificate-extra-sans option.

        Returns:
            bool: True if config value is valid, False if invalid.
        """
        if not (extra_sans_config := self.state.config.get("certificate-extra-sans")):
            return True

        extra_sans = [san.strip() for san in extra_sans_config.split(",")]
        allowed = re.compile(r"(?!-)[A-Z0-9-{}]{1,63}(?<!-)$", re.IGNORECASE)

        for san in extra_sans:
            # validation for ip addresses
            if len(san.split(".")) == 4:
                try:
                    ip_address(san)
                except ValueError:
                    logger.error(f"certificate-extra-sans configuration is invalid for ip {san}")
                    return False
            # validation for dns names
            elif not all(allowed.match(x) for x in san.split(".")):
                logger.error(f"certificate-extra-sans configuration is invalid for dns {san}")
                return False

        return True

    def get_current_sans(self, tls_type: TLSType) -> Dict[str, set[str]]:
        """Get the current SANs for a unit's cert."""
        if tls_type == TLSType.CLIENT:
            cert_file = self.workload.paths.tls.client_cert
        else:
            cert_file = self.workload.paths.tls.peer_cert

        sans_ip = set()
        sans_dns = set()
        if not (
            san_lines := self.workload.exec(
                [
                    "openssl",
                    "x509",
                    "-ext",
                    "subjectAltName",
                    "-noout",
                    "-in",
                    cert_file,
                ]
            ).splitlines()
        ):
            return {"sans_ip": sans_ip, "sans_dns": sans_dns}

        for line in san_lines:
            for sans in line.split(", "):
                san_type, san_value = sans.split(":")

                if san_type.strip() == "DNS":
                    sans_dns.add(san_value)
                if san_type.strip() == "IP Address":
                    sans_ip.add(san_value)

        return {"sans_ip": sans_ip, "sans_dns": sans_dns}

    def certificate_sans_updated(self, tls_type: TLSType) -> bool:
        """Check current certificate sans and determine if certificate requires update.

        Returns:
            bool: True if certificate sans have changed, False if they are still the same.
        """
        current_sans = self.get_current_sans(tls_type)
        new_sans_ip = self.build_sans_ip(tls_type)
        new_sans_dns = self.build_sans_dns()

        if new_sans_ip ^ current_sans["sans_ip"] or new_sans_dns ^ current_sans["sans_dns"]:
            return True

        return False

    def collect_client_cas(self) -> set[str]:
        """Collect client CAs.

        Returns:
            set[str]: The client CAs.
        """
        cas: set[str] = {self.state.tls_client_certificate.ca.raw}

        # managed users cas
        for relation in self.state.etcd_provides.relations:
            mtls_cert = self.state.etcd_provides.fetch_relation_field(relation.id, "mtls-cert")
            logger.debug(
                f"Collecting CA from relation {relation.id}, chain exists: {bool(mtls_cert)}"
            )
            if mtls_cert and is_leaf_certificate_valid(mtls_cert):
                cas.add(leaf_certificate(mtls_cert))

        # certificate transfer cas
        cas.update(self.state.tls_certificate_transfer_certificates)

        return cas

    def collect_peer_ca(self) -> str:
        """Collect peer CA.

        Returns:
            str: The peer CA.
        """
        return self.state.tls_peer_certificate.ca.raw
