#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""

import json
import logging
import socket
import subprocess
from pathlib import Path

import ops
from charmlibs import snap
from charmlibs.interfaces.tls_certificates import (
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    TLSCertificatesRequiresV4,
)
from constants import SNAP_DIR, SNAP_NAME
from etcd_requires import EtcdRequiresV0, EtcdRequiresV1
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)


class RefreshTLSCertificatesEvent(ops.EventBase):
    """Event for refreshing peer TLS certificates."""


class RequirerCharm(ops.CharmBase):
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
                    common_name=common_name,
                    sans_ip=frozenset({socket.gethostbyname(socket.gethostname())}),
                    sans_dns=frozenset({self.unit.name, socket.gethostname()}),
                )
                for common_name in self.common_names
            ],
            refresh_events=[self.refresh_tls_certificates_event],
        )

        match self.data_interfaces_version:
            case 0:
                self.etcd_requires = EtcdRequiresV0(self)
            case 1:
                self.etcd_requires = EtcdRequiresV1(self)
            case _:
                self.app.status = ops.BlockedStatus(
                    f"Invalid data-interfaces-version config value: {self.data_interfaces_version}. Only 0 and 1 are supported."
                )

        # TLSCertificatesRequiresV4 events
        framework.observe(
            self.certificates.on.certificate_available, self._on_certificate_available
        )

        # Charm events
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.update_mtls_certs_action, self._on_update_action)
        framework.observe(self.on.put_action, self._on_put_action)
        framework.observe(self.on.get_action, self._on_get_action)
        framework.observe(self.on.get_credentials_action, self._on_get_credentials_action)
        framework.observe(self.on.config_changed, self._config_changed)
        framework.observe(self.on.get_certificates_action, self._on_get_certificates_action)

    @property
    def data_interfaces_version(self) -> int | None:
        """Return the data interfaces version from config."""
        version = self.config.get("data-interfaces-version")
        if version is None or not isinstance(version, int) or version not in [0, 1]:
            return None
        return version

    @property
    def common_names(self) -> list[str]:
        """Return the common names for the client certificates."""
        if self.data_interfaces_version == 1:
            return [
                "client1.requirer-charm",
                "client2.requirer-charm",
            ]
        return ["requirer-charm"]

    @property
    def server_ca_chain(self) -> str | None:
        """Return the server CA chain."""
        try:
            ca_chain = Path(f"{SNAP_DIR}/ca.pem").read_text().strip()
        except FileNotFoundError:
            return None
        return ca_chain

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
        """Handle update mtls certificate action."""
        # client relation
        if not self.etcd_requires.etcd_relation:
            event.fail("etcd-client relation not found")
            return

        certs, _ = self.certificates.get_assigned_certificates()
        if not certs:
            event.fail("No certificates available")
            return

        for cert in certs:
            self.certificates.renew_certificate(cert)

        event.set_results({"message": "certificates renewed"})

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Handle certificate available event."""
        logger.info("Certificate available")
        certs, private_key = self.certificates.get_assigned_certificates()
        if not certs or not private_key:
            logger.error("No certificates available")
            return

        if self.etcd_requires.etcd_relation:
            self.etcd_requires.update_requests_from_certs(
                [cert.ca if self.send_ca_option else cert.certificate for cert in certs]
            )

    def _config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config changed event."""
        self.refresh_tls_certificates_event.emit()

    def _on_put_action(self, event: ops.ActionEvent) -> None:
        """Handle put action."""
        if not self.etcd_requires.etcd_relation:
            event.fail("The action can be run only after relation is created.")
            event.set_results({"ok": False})
            return
        orig_key = str(event.params.get("key", ""))
        value = str(event.params.get("value", ""))
        user = str(event.params.get("user", ""))
        if not orig_key or not value or not user:
            event.fail("Key, value and user parameters are required.")
            event.set_results({"ok": False})
            return

        uris = self.etcd_requires.etcd_uris

        if not uris:
            event.fail("No uris available")
            event.set_results({"ok": False})
            return

        certs, private_key = self.certificates.get_assigned_certificates()
        if not certs or not private_key:
            event.fail("No certificates available")
            return

        for cert in certs:
            if cert.certificate.common_name != user:
                pass
            Path(SNAP_DIR).mkdir(exist_ok=True)
            Path(f"{SNAP_DIR}/client.pem").write_text(cert.certificate.raw)
            Path(f"{SNAP_DIR}/client.key").write_text(private_key.raw)
            if result := _put(uris, orig_key, value):
                event.set_results(
                    {
                        "ok": True,
                        "result": json.dumps(result),
                    }
                )
            else:
                event.set_results(
                    {
                        "ok": False,
                        "result": json.dumps(result),
                    }
                )
                event.fail(f"etcdctl put failed for certificate with common name: {user}")

    def _on_get_action(self, event: ops.ActionEvent) -> None:
        """Handle get action."""
        certs, private_key = self.certificates.get_assigned_certificates()
        if not certs or not private_key:
            event.fail("No certificates available")
            return

        if not self.etcd_requires.etcd_relation:
            event.fail("The action can be run only after relation is created.")
            event.set_results({"ok": False})
            return

        orig_key = str(event.params.get("key", ""))
        user = str(event.params.get("user", ""))
        if not orig_key or not user:
            event.fail("Key and user parameters are required.")
            event.set_results({"ok": False})
            return

        uris = self.etcd_requires.etcd_uris
        if not uris:
            event.fail("No uris available")
            event.set_results({"ok": False})
            return

        for cert in certs:
            if cert.certificate.common_name != user:
                pass
            Path(SNAP_DIR).mkdir(exist_ok=True)
            Path(f"{SNAP_DIR}/client.pem").write_text(cert.certificate.raw)
            Path(f"{SNAP_DIR}/client.key").write_text(private_key.raw)
            if result := _get(uris, orig_key):
                event.set_results(
                    {
                        "ok": True,
                        "result": json.dumps(result),
                    }
                )
            else:
                event.set_results(
                    {
                        "ok": False,
                        "result": json.dumps(result),
                    }
                )
                event.fail(f"etcdctl get failed for certificate with common name: {user}")

    def _on_get_credentials_action(self, event: ops.ActionEvent) -> None:
        """Return the credentials an action response."""
        if not self.server_ca_chain:
            event.fail(
                "The server CA chain is not available. Please wait for the server to provide it."
            )
            event.set_results({"ok": False})
            return

        if not self.etcd_requires.etcd_relation:
            event.fail("The action can be run only after relation is created.")
            event.set_results({"ok": False})
            return

        if not (credentials := self.etcd_requires.credentials):
            event.fail("No credentials available")
            event.set_results({"ok": False})
            return

        event.set_results(
            {
                "ok": True,
                **credentials,
            }
        )

    def _on_get_certificates_action(self, event: ops.ActionEvent) -> None:
        """Return the certificate an action response."""
        certs, _ = self.certificates.get_assigned_certificates()
        if not certs:
            event.fail("No certificates available")
            return

        certs_to_send = [
            cert.ca.raw if self.send_ca_option else cert.certificate.raw for cert in certs
        ]
        event.set_results(
            {
                "certificates": json.dumps(certs_to_send),
            }
        )

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

    def get_certificate_of_common_name(self, common_name: str) -> str | None:
        """Return the certificate for a given common name."""
        certs, _ = self.certificates.get_assigned_certificates()
        if not certs:
            return None
        for cert in certs:
            if cert.certificate.common_name == common_name:
                return cert.ca.raw if self.send_ca_option else cert.certificate.raw
        return None


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


if __name__ == "__main__":  # pragma: nocover
    ops.main(RequirerCharm)
