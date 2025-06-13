#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
import socket
import subprocess
from pathlib import Path

import ops
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseEndpointsChangedEvent,
    EtcdReadyEvent,
    EtcdRequires,
)
from charms.operator_libs_linux.v2 import snap
from charms.tls_certificates_interface.v4.tls_certificates import (
    Certificate,
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    TLSCertificatesRequiresV4,
)
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

SNAP_NAME = "charmed-etcd"
SNAP_DIR = "/var/snap/charmed-etcd/common"


class RefreshTLSCertificatesEvent(ops.EventBase):
    """Event for refreshing peer TLS certificates."""


class RequirerCharmCharm(ops.CharmBase):
    """Charm the application."""

    refresh_tls_certificates_event = ops.EventSource(RefreshTLSCertificatesEvent)

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.etcd_snap = snap.SnapCache()[SNAP_NAME]
        self.certificates = TLSCertificatesRequiresV4(
            self,
            "certificates",
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=self.common_name,
                    sans_ip=frozenset({socket.gethostbyname(socket.gethostname())}),
                    sans_dns=frozenset({self.unit.name, socket.gethostname()}),
                ),
            ],
            refresh_events=[self.refresh_tls_certificates_event],
        )

        self.etcd_requires = EtcdRequires(
            self,
            relation_name="etcd-client",
            prefix="/test/",
            mtls_cert=self.raw_certificate,
        )

        # EtcdRequires events
        framework.observe(self.etcd_requires.on.endpoints_changed, self._on_endpoints_changed)
        framework.observe(self.etcd_requires.on.etcd_ready, self._on_etcd_ready)

        # TLSCertificatesRequiresV4 events
        framework.observe(
            self.certificates.on.certificate_available, self._on_certificate_available
        )

        # Charm events
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.update_common_name_action, self._on_update_action)
        framework.observe(self.on.put_action, self._on_put_action)
        framework.observe(self.on.get_action, self._on_get_action)
        framework.observe(self.on.get_credentials_action, self._on_get_credentials_action)
        framework.observe(self.on.config_changed, self._on_certificate_available)
        framework.observe(self.on.get_certificate_action, self._on_get_certificate_action)

    @property
    def common_name(self) -> str:
        """Return the common name for the certificate."""
        if not self.etcd_relation:
            return "requirer-charm"
        mtls_cert = self.etcd_requires.fetch_my_relation_field(self.etcd_relation.id, "mtls-cert")
        if not mtls_cert:
            return "requirer-charm"

        return _get_common_name_from_chain(mtls_cert)

    @property
    def server_ca_chain(self) -> str | None:
        """Return the server CA chain."""
        try:
            ca_chain = Path(f"{SNAP_DIR}/ca.pem").read_text().strip()
        except FileNotFoundError:
            return None
        return ca_chain

    @property
    def ca_chain(self) -> str | None:
        """Return the CA chain."""
        certs, _ = self.certificates.get_assigned_certificates()
        if not certs:
            return None
        return "\n".join(cert.raw for cert in certs[0].chain[::])

    @property
    def ca_cert(self) -> str | None:
        """Return the CA certificate."""
        certs, _ = self.certificates.get_assigned_certificates()
        if not certs:
            return None
        return certs[0].ca.raw

    @property
    def raw_certificate(self) -> str | None:
        """Return the raw certificate."""
        raw_cert = self.ca_cert if self.send_ca_option else self.ca_chain
        return raw_cert or ""

    @property
    def etcd_relation(self) -> ops.Relation | None:
        """Return the etcd relation if present."""
        if not hasattr(self, "etcd_requires"):
            return None
        return self.etcd_requires.relations[0] if len(self.etcd_requires.relations) else None

    @property
    def send_ca_option(self) -> bool:
        """Return True if the CA chain is available."""
        return bool(self.config.get("send-ca-cert", False))

    def _on_start(self, event: ops.StartEvent) -> None:
        """Handle start event."""
        self.unit.status = ops.ActiveStatus()

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle install event."""
        # install the etcd snap
        if not self._install_etcd_snap():
            self.unit.status = ops.BlockedStatus("Failed to install etcd snap")
            return

    def _on_update_action(self, event: ops.ActionEvent) -> None:
        """Handle update common name action."""
        # client relation
        if not (relation := self.model.get_relation("etcd-client")):
            event.fail("etcd-client relation not found")
            return

        if event.params.get("chain"):
            ca = event.params["chain"].replace("\\n", "\n")
            self.etcd_requires.set_mtls_cert(relation.id, ca)

        event.set_results({"message": "chain updated on data bag"})

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Handle certificate available event."""
        logger.info("Certificate available")
        certs, private_key = self.certificates.get_assigned_certificates()
        if not certs or not private_key:
            logger.error("No certificates available")
            return

        cert = certs[0]
        Path(SNAP_DIR).mkdir(exist_ok=True)
        Path(f"{SNAP_DIR}/client.pem").write_text(cert.certificate.raw)
        Path(f"{SNAP_DIR}/client.key").write_text(private_key.raw)

        if relation := self.model.get_relation("etcd-client"):
            raw_cert = self.raw_certificate or cert.certificate.raw
            self.etcd_requires.set_mtls_cert(relation.id, raw_cert)

    def _on_etcd_ready(self, event: EtcdReadyEvent) -> None:
        """Handle etcd ready event."""
        logger.info("etcd ready")
        if not event.tls_ca:
            logger.error("No server CA chain available")
            return
        if not event.username:
            logger.error("No username available")
            return
        Path(SNAP_DIR).mkdir(exist_ok=True)
        Path(f"{SNAP_DIR}/ca.pem").write_text(event.tls_ca)

    def _on_endpoints_changed(self, event: DatabaseEndpointsChangedEvent) -> None:
        """Handle etcd client relation data changed event."""
        logger.info("Endpoints changed: %s", event.endpoints)
        if not event.endpoints:
            logger.error("No endpoints available")
            return

    def _on_put_action(self, event: ops.ActionEvent) -> None:
        """Handle put action."""
        if not self.etcd_relation:
            event.fail("The action can be run only after relation is created.")
            event.set_results({"ok": False})
            return
        key = event.params["key"]
        value = event.params["value"]
        uris = self.etcd_requires.fetch_relation_field(self.etcd_relation.id, "uris")
        if not uris:
            event.fail("No uris available")
            event.set_results({"ok": False})
            return
        if result := _put(uris, key, value):
            event.set_results({"message": result})
        else:
            event.fail("etcdctl put failed")

    def _on_get_action(self, event: ops.ActionEvent) -> None:
        """Handle get action."""
        certs, _ = self.certificates.get_assigned_certificates()
        if not certs:
            event.fail("No certificates available")
            return
        if not self.etcd_relation:
            event.fail("The action can be run only after relation is created.")
            event.set_results({"ok": False})
            return
        uris = self.etcd_requires.fetch_relation_field(self.etcd_relation.id, "uris")
        if not uris:
            event.fail("No uris available")
            event.set_results({"ok": False})
            return
        certs[0].chain
        key = event.params["key"]
        result = _get(uris, key)
        if result:
            event.set_results({"message": result})
        else:
            event.fail("etcdctl get failed")

    def _on_get_credentials_action(self, event: ops.ActionEvent) -> None:
        """Return the credentials an action response."""
        if not self.server_ca_chain:
            event.fail(
                "The server CA chain is not available. Please wait for the server to provide it."
            )
            event.set_results({"ok": False})
            return

        if not self.etcd_relation:
            event.fail("The action can be run only after relation is created.")
            event.set_results({"ok": False})
            return

        result: dict = {"ok": True}

        result.update(
            {
                "username": self.etcd_requires.fetch_relation_field(
                    self.etcd_relation.id, "username"
                ),
                "uris": self.etcd_requires.fetch_relation_field(self.etcd_relation.id, "uris"),
                "endpoints": self.etcd_requires.fetch_relation_field(
                    self.etcd_relation.id, "endpoints"
                ),
                "version": self.etcd_requires.fetch_relation_field(
                    self.etcd_relation.id, "version"
                ),
                "tls-ca": self.etcd_requires.fetch_relation_field(self.etcd_relation.id, "tls-ca"),
            }
        )

        event.set_results(result)

    def _on_get_certificate_action(self, event: ops.ActionEvent) -> None:
        """Return the certificate an action response."""
        if self.send_ca_option:
            event.set_results({"certificate": self.ca_cert})
        else:
            certs, _ = self.certificates.get_assigned_certificates()
            if not certs:
                event.fail("No certificates available")
                return
            event.set_results({"certificate": certs[0].certificate.raw})

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True)
    def _install_etcd_snap(self) -> bool:
        """Install the etcd snap."""
        try:
            self.etcd_snap.ensure(snap.SnapState.Present, channel="3.6/edge")
            self.etcd_snap.hold()
            return True
        except snap.SnapError as e:
            logger.error(str(e))
            return False


def _put(endpoints: str, key: str, value: str) -> str | None:
    """Put a key value pair in etcd."""
    if (
        not Path(f"{SNAP_DIR}/client.pem").exists()
        or not Path(f"{SNAP_DIR}/client.key").exists()
        or not Path(f"{SNAP_DIR}/ca.pem").exists()
    ):
        logger.error("No client certificates available")
        return

    try:
        output = subprocess.check_output(
            [
                "charmed-etcd.etcdctl",
                "--endpoints",
                endpoints,
                "--cert",
                f"{SNAP_DIR}/client.pem",
                "--key",
                f"{SNAP_DIR}/client.key",
                "--cacert",
                f"{SNAP_DIR}/ca.pem",
                "put",
                key,
                value,
            ],
        )
    except subprocess.CalledProcessError:
        logger.error("etcdctl put failed")
        return None

    return output.decode("utf-8").strip()


def _get(endpoints: str, key: str) -> str | None:
    """Get a key value pair from etcd."""
    if (
        not Path(f"{SNAP_DIR}/client.pem").exists()
        or not Path(f"{SNAP_DIR}/client.key").exists()
        or not Path(f"{SNAP_DIR}/ca.pem").exists()
    ):
        logger.error("No client certificates available")
        return

    try:
        output = subprocess.check_output(
            [
                "charmed-etcd.etcdctl",
                "--endpoints",
                endpoints,
                "--cert",
                f"{SNAP_DIR}/client.pem",
                "--key",
                f"{SNAP_DIR}/client.key",
                "--cacert",
                f"{SNAP_DIR}/ca.pem",
                "get",
                key,
            ],
        )
    except subprocess.CalledProcessError:
        logger.error("etcdctl get failed")
        return

    return output.decode("utf-8").strip()


def _get_common_name_from_chain(mtls_cert: str) -> str:
    """Get common name from chain."""
    raw_cas = mtls_cert.split("-----END CERTIFICATE-----")
    raw_cas.remove("")
    # add the marker back to the certificate
    # we take the last certificate from the provided mtls_cert, assuming this is the client cert
    cert = raw_cas[-1].strip() + "\n-----END CERTIFICATE-----"
    return Certificate.from_string(cert).common_name


if __name__ == "__main__":  # pragma: nocover
    ops.main(RequirerCharmCharm)
