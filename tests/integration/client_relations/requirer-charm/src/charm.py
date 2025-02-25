#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
import socket
import subprocess
import tarfile
from pathlib import Path
from urllib.request import urlretrieve

import ops
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseEndpointsChangedEvent,
    EtcdRequires,
    ServerCAUpdatedEvent,
)
from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    TLSCertificatesRequiresV4,
)

logger = logging.getLogger(__name__)

WORK_DIR = "./tmp"


class RefreshTLSCertificatesEvent(ops.EventBase):
    """Event for refreshing peer TLS certificates."""


class RequirerCharmCharm(ops.CharmBase):
    """Charm the application."""

    refresh_tls_certificates_event = ops.EventSource(RefreshTLSCertificatesEvent)

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
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
            keys_prefix="/test/",
            ca_chain=self.ca_chain,
            common_name=self.common_name,
        )

        # EtcdRequires events
        framework.observe(self.etcd_requires.on.endpoints_changed, self._on_endpoints_changed)
        framework.observe(self.etcd_requires.on.ca_chain_updated, self._on_ca_chain_updated)

        # TLSCertificatesRequiresV4 events
        framework.observe(
            self.certificates.on.certificate_available, self._on_certificate_available
        )

        # Charm events
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.update_action, self._on_update_action)
        framework.observe(self.on.put_action, self._on_put_action)
        framework.observe(self.on.get_action, self._on_get_action)

    @property
    def common_name(self):
        try:
            common_name = Path(f"{WORK_DIR}/common_name.txt").read_text().strip()
        except FileNotFoundError:
            common_name = ""
        if not common_name:
            common_name = "requirer-charm"
            Path(WORK_DIR).mkdir(exist_ok=True)
            Path(f"{WORK_DIR}/common_name.txt").write_text(common_name)
        return common_name

    @property
    def server_ca_chain(self):
        try:
            ca_chain = Path(f"{WORK_DIR}/ca.pem").read_text().strip()
        except FileNotFoundError:
            return None
        return ca_chain

    @property
    def ca_chain(self):
        certs, _ = self.certificates.get_assigned_certificates()
        if not certs:
            return None
        return certs[0].ca.raw

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        self.unit.status = ops.ActiveStatus()
        # download etcdctl binary
        urlretrieve(
            "https://github.com/etcd-io/etcd/releases/download/v3.5.18/etcd-v3.5.18-linux-amd64.tar.gz",
            "etcd-v3.5.18-linux-amd64.tar.gz",
        )
        # extract etcdctl binary
        with tarfile.open("etcd-v3.5.18-linux-amd64.tar.gz", "r:gz") as tar:
            tar.extractall()
        Path("etcd-v3.5.18-linux-amd64/etcdctl").rename("etcdctl")

    def _on_update_action(self, event: ops.ActionEvent):
        """Handle update common name action."""
        # client relation
        relation = self.model.get_relation("etcd-client")
        if not relation:
            event.fail("etcd-client relation not found")
            return

        if event.params.get("common-name"):
            common_name = event.params["common-name"]
            Path(WORK_DIR).mkdir(exist_ok=True)
            Path(f"{WORK_DIR}/common_name.txt").write_text(common_name)
            self.certificates.certificate_requests = [
                CertificateRequestAttributes(
                    common_name=self.common_name,
                    sans_ip=frozenset({socket.gethostbyname(socket.gethostname())}),
                    sans_dns=frozenset({self.unit.name, socket.gethostname()}),
                ),
            ]
            self.refresh_tls_certificates_event.emit()

        if event.params.get("ca"):
            ca = event.params["ca"].replace("\\n", "\n")
            self.etcd_requires.set_ca_chain(relation.id, ca)

        event.set_results({"message": "databag updated"})

    def _on_certificate_available(self, event: CertificateAvailableEvent):
        """Handle certificate available event."""
        logger.info("Certificate available")
        certs, private_key = self.certificates.get_assigned_certificates()
        if not certs or not private_key:
            logger.error("No certificates available")
            return

        cert = certs[0]
        Path(WORK_DIR).mkdir(exist_ok=True)
        Path(f"{WORK_DIR}/client.pem").write_text(cert.certificate.raw)
        Path(f"{WORK_DIR}/client.key").write_text(private_key.raw)

        relation = self.model.get_relation("etcd-client")
        if relation:
            self.etcd_requires.set_common_name(relation.id, self.common_name)
            self.etcd_requires.set_ca_chain(relation.id, cert.ca.raw)

    def _on_ca_chain_updated(self, event: ServerCAUpdatedEvent):
        """Handle server CA updated event."""
        logger.info("Server CA updated")
        if not event.ca_chain:
            logger.error("No server CA chain available")
            return
        Path(WORK_DIR).mkdir(exist_ok=True)
        Path(f"{WORK_DIR}/ca.pem").write_text(event.ca_chain)

    def _on_endpoints_changed(self, event: DatabaseEndpointsChangedEvent):
        """Handle etcd client relation data changed event."""
        logger.info("Endpoints changed: %s", event.endpoints)
        if not event.endpoints:
            logger.error("No endpoints available")
            return
        Path(WORK_DIR).mkdir(exist_ok=True)
        Path(f"{WORK_DIR}/endpoints.txt").write_text(event.endpoints)

    def _on_put_action(self, event: ops.ActionEvent):
        """Handle put action."""
        key = event.params["key"]
        value = event.params["value"]
        if result := _put(key, value):
            event.set_results({"message": result})
        else:
            event.fail("etcdctl put failed")

    def _on_get_action(self, event: ops.ActionEvent):
        """Handle get action."""
        key = event.params["key"]
        result = _get(key)
        if result:
            event.set_results({"message": result})
        else:
            event.fail("etcdctl get failed")


def _put(key: str, value: str):
    """Put a key value pair in etcd."""
    endpoints = Path(f"{WORK_DIR}/endpoints.txt").read_text().strip()
    if not endpoints:
        logger.error("No endpoints available")
        return
    if (
        not Path(f"{WORK_DIR}/client.pem").exists()
        or not Path(f"{WORK_DIR}/client.key").exists()
        or not Path(f"{WORK_DIR}/ca.pem").exists()
    ):
        logger.error("No client certificates available")
        return

    try:
        output = subprocess.check_output(
            [
                "./etcdctl",
                "--endpoints",
                endpoints,
                "--cert",
                f"{WORK_DIR}/client.pem",
                "--key",
                f"{WORK_DIR}/client.key",
                "--cacert",
                f"{WORK_DIR}/ca.pem",
                "put",
                key,
                value,
            ],
        )
    except subprocess.CalledProcessError:
        logger.error("etcdctl put failed")
        return None

    return output.decode("utf-8").strip()


def _get(key: str) -> str:
    """Get a key value pair from etcd."""
    endpoints = Path(f"{WORK_DIR}/endpoints.txt").read_text().strip()
    if not endpoints:
        logger.error("No endpoints available")
        return ""
    if (
        not Path(f"{WORK_DIR}/client.pem").exists()
        or not Path(f"{WORK_DIR}/client.key").exists()
        or not Path(f"{WORK_DIR}/ca.pem").exists()
    ):
        logger.error("No client certificates available")
        return ""

    try:
        output = subprocess.check_output(
            [
                "./etcdctl",
                "--endpoints",
                endpoints,
                "--cert",
                f"{WORK_DIR}/client.pem",
                "--key",
                f"{WORK_DIR}/client.key",
                "--cacert",
                f"{WORK_DIR}/ca.pem",
                "get",
                key,
            ],
        )
    except subprocess.CalledProcessError:
        logger.error("etcdctl get failed")
        return ""

    return output.decode("utf-8").strip()


if __name__ == "__main__":  # pragma: nocover
    ops.main(RequirerCharmCharm)
