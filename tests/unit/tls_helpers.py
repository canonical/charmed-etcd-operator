# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
"""Helper functions for TLS-related tests. Copied from the tls lib."""

import logging

from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

logger = logging.getLogger(__name__)


def generate_certificate_request_extensions(
    authority_key_identifier: bytes,
    csr: x509.CertificateSigningRequest,
    is_ca: bool,
) -> list[x509.Extension]:
    """Generate a list of certificate extensions from a CSR and other known information.

    Args:
        authority_key_identifier (bytes): Authority key identifier
        csr (x509.CertificateSigningRequest): CSR
        is_ca (bool): Whether the certificate is a CA certificate

    Returns:
        List[x509.Extension]: List of extensions
    """
    cert_extensions_list: list[x509.Extension] = [
        x509.Extension(
            oid=ExtensionOID.AUTHORITY_KEY_IDENTIFIER,
            value=x509.AuthorityKeyIdentifier(
                key_identifier=authority_key_identifier,
                authority_cert_issuer=None,
                authority_cert_serial_number=None,
            ),
            critical=False,
        ),
        x509.Extension(
            oid=ExtensionOID.SUBJECT_KEY_IDENTIFIER,
            value=x509.SubjectKeyIdentifier.from_public_key(csr.public_key()),
            critical=False,
        ),
        x509.Extension(
            oid=ExtensionOID.BASIC_CONSTRAINTS,
            critical=True,
            value=x509.BasicConstraints(ca=is_ca, path_length=None),
        ),
    ]
    if sans := _generate_subject_alternative_name_extension(csr):
        cert_extensions_list.append(sans)

    if is_ca:
        cert_extensions_list.append(
            x509.Extension(
                ExtensionOID.KEY_USAGE,
                critical=True,
                value=x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
            )
        )

    existing_oids = {ext.oid for ext in cert_extensions_list}
    for extension in csr.extensions:
        if extension.oid == ExtensionOID.SUBJECT_ALTERNATIVE_NAME:
            continue
        if extension.oid in existing_oids:
            logger.warning("Extension %s is managed by the TLS provider, ignoring.", extension.oid)
            continue
        cert_extensions_list.append(extension)

    return cert_extensions_list


def _generate_subject_alternative_name_extension(
    csr: x509.CertificateSigningRequest,
) -> x509.Extension | None:
    sans: list[x509.GeneralName] = []
    try:
        loaded_san_ext = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans.extend(
            [x509.DNSName(name) for name in loaded_san_ext.value.get_values_for_type(x509.DNSName)]
        )
        sans.extend(
            [x509.IPAddress(ip) for ip in loaded_san_ext.value.get_values_for_type(x509.IPAddress)]
        )
        sans.extend(
            [
                x509.RegisteredID(oid)
                for oid in loaded_san_ext.value.get_values_for_type(x509.RegisteredID)
            ]
        )
        sans.extend(
            [
                x509.RFC822Name(name)
                for name in loaded_san_ext.value.get_values_for_type(x509.RFC822Name)
            ]
        )
    except x509.ExtensionNotFound:
        pass
    # If email is present in the CSR Subject, make sure it is also in the SANS
    # to conform to RFC 5280.
    email = csr.subject.get_attributes_for_oid(NameOID.EMAIL_ADDRESS)
    if email:
        email_rfc822 = x509.RFC822Name(str(email[0].value))
        if email_rfc822 not in sans:
            sans.append(email_rfc822)

    return (
        x509.Extension(
            oid=ExtensionOID.SUBJECT_ALTERNATIVE_NAME,
            critical=False,
            value=x509.SubjectAlternativeName(sans),
        )
        if sans
        else None
    )
