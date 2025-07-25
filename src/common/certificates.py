# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utility functions related to certificates."""

import logging

from charms.tls_certificates_interface.v4.tls_certificates import Certificate
from cryptography import x509

logger = logging.getLogger(__name__)


def leaf_certificate(certificate_chain: str) -> str:
    """Extract the leaf certificate from a certificate chain.

    Args:
        certificate_chain (str): The certificate chain.

    Returns:
        str: The leaf certificate.
    """
    certificates = certificate_chain.split("-----END CERTIFICATE-----")
    if not certificates or len(certificates) < 2:
        raise ValueError("Invalid certificate chain provided.")
    return certificates[0].strip() + "\n-----END CERTIFICATE-----"


def is_leaf_certificate_valid(mtls_cert: str) -> bool:
    """Validate the leaf certificate.

    Args:
        mtls_cert (str): The mtls chain.

    Returns:
        (bool): True if the certificate is not a CA.
    """
    leaf_cert = leaf_certificate(mtls_cert)
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
