#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling external clients."""

import json
import logging
from pathlib import Path

from charms.tls_certificates_interface.v4.tls_certificates import Certificate
from cryptography import x509

from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import SUBSTRATES

logger = logging.getLogger(__name__)

WORKING_DIR = Path(__file__).absolute().parent


class ExternalClientsManager:
    """Handle the external clients related logic."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
        substrate: SUBSTRATES,
    ):
        self.state = state
        self.workload = workload
        self.substrate = substrate

    def remove_managed_user(self, relation_id: int) -> None:
        """Remove the user.

        Args:
            relation_id (int): The relation id.
        """
        managed_users = self.state.cluster.managed_users
        del managed_users[relation_id]
        self.state.cluster.update(
            {
                "managed_users": json.dumps(managed_users),
            }
        )

    def add_managed_user(self, relation_id: int, common_name: str) -> None:
        """Add the user.

        Args:
            relation_id (int): The relation id.
            common_name (str): The common name.
        """
        managed_users = self.state.cluster.managed_users
        managed_users[relation_id] = common_name

        self.state.cluster.update(
            {
                "managed_users": json.dumps(managed_users),
            }
        )

    def get_relation_managed_user(self, relation_id: int) -> str | None:
        """Get the relation's managed user.

        Args:
            relation_id (int): The relation id.

        Returns:
            (str): The managed user.
        """
        return self.state.cluster.managed_users.get(relation_id)

    def get_common_name_from_chain(self, mtls_cert: str) -> str:
        """Get the common name from the mtls chain.

        Args:
            mtls_cert (str): The mtls chain.

        Returns:
            (str): The common name.
        """
        # split the certificates by the end of the certificate marker and keep the marker in the cert
        raw_cas = mtls_cert.split("-----END CERTIFICATE-----")
        # add the marker back to the certificate
        cert = raw_cas[0].strip() + "\n-----END CERTIFICATE-----"
        return Certificate.from_string(cert).common_name

    def is_leaf_certificate_valid(self, mtls_cert: str) -> bool:
        """Validate the leaf certificate.

        Args:
            mtls_cert (str): The mtls chain.

        Returns:
            (bool): True if the certificate is not a CA.
        """
        # split the certificates by the end of the certificate marker and keep the marker in the cert
        raw_cas = mtls_cert.split("-----END CERTIFICATE-----")
        # add the marker back to the certificate
        leaf_cert = raw_cas[0].strip() + "\n-----END CERTIFICATE-----"
        logger.debug(f"Leaf certificate is a CA? {Certificate.from_string(leaf_cert).is_ca}")
        certificate = x509.load_pem_x509_certificate(data=leaf_cert.encode())
        # check if the certificate is a CA
        try:
            basic_constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
        except x509.ExtensionNotFound:
            return False
        # check if the certificate can sign other certificates
        try:
            key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
        except x509.ExtensionNotFound:
            return not basic_constraints.ca

        return not (key_usage.key_cert_sign or key_usage.crl_sign or basic_constraints.ca)
