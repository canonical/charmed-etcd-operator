#!/usr/bin/env python3
# Copyright 2025 Ubuntu
# See LICENSE file for licensing details.

import logging
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, override

import ops
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseEndpointsChangedEvent,
    EtcdReadyEvent,
)
from charms.data_platform_libs.v0.data_interfaces import EtcdRequires as EtcdRequiresV0Base
from charms.data_platform_libs.v1.data_interfaces import (
    DataContractV1,
    RequirerCommonModel,
    RequirerDataContractV1,
    ResourceCreatedEvent,
    ResourceEndpointsChangedEvent,
    ResourceProviderModel,
    ResourceRequirerEventHandler,
    build_model,
)
from charms.tls_certificates_interface.v4.tls_certificates import Certificate
from constants import SNAP_DIR

if TYPE_CHECKING:
    from charm import RequirerCharm

logger = logging.getLogger(__name__)


class EtcdRequires(ops.framework.Object):
    """Common interface for etcd requirer relation."""

    def __init__(self, charm: "RequirerCharm") -> None:
        super().__init__(charm, "requirer-etcd")
        self.charm = charm

    @abstractmethod
    def _on_endpoints_changed(self, event) -> None:
        """Handle endpoints changed event."""
        pass

    @abstractmethod
    def _on_resource_created(self, event) -> None:
        """Handle resource created event."""
        pass

    @abstractmethod
    def update_mtls_certs(self, cert: str) -> None:
        """Set the mtls cert in the relation data bag."""
        pass

    @abstractmethod
    def update_requests_from_certs(self, certs: list[Certificate]) -> None:
        """Update the requests in the relation data bag from the assigned certificates."""
        pass

    @property
    @abstractmethod
    def etcd_relation(self) -> ops.Relation | None:
        """Return the etcd relation if present."""
        pass

    @property
    @abstractmethod
    def etcd_uris(self) -> str | None:
        """Return the etcd uris."""
        pass

    @property
    @abstractmethod
    def credentials(self) -> dict[str, str | None] | None:
        """Return the etcd credentials."""
        pass


class EtcdRequiresV1(EtcdRequires):
    """EtcdRequires implementation for data interfaces version 1."""

    def __init__(
        self,
        charm: "RequirerCharm",
    ) -> None:
        super().__init__(charm=charm)
        self.etcd_interface = ResourceRequirerEventHandler(
            self.charm,
            relation_name="etcd-client",
            requests=self.client_requests,
            response_model=ResourceProviderModel,
        )

        self.framework.observe(
            self.etcd_interface.on.endpoints_changed, self._on_endpoints_changed
        )
        self.framework.observe(self.etcd_interface.on.resource_created, self._on_resource_created)

    @override
    def _on_endpoints_changed(
        self, event: ResourceEndpointsChangedEvent[ResourceProviderModel]
    ) -> None:
        """Handle etcd client relation data changed event."""
        response = event.response
        logger.info("Endpoints changed: %s", response.endpoints)
        if not response.endpoints:
            logger.error("No endpoints available")

    @override
    def _on_resource_created(self, event: ResourceCreatedEvent[ResourceProviderModel]) -> None:
        """Handle resource created event."""
        logger.info("Resource created")
        response = event.response
        if not response.tls_ca:
            logger.error("No server CA chain available")
            return
        if not response.username:
            logger.error("No username available")
            return
        Path(SNAP_DIR).mkdir(exist_ok=True)
        Path(f"{SNAP_DIR}/ca.pem").write_text(response.tls_ca)

    @override
    def update_mtls_certs(self, cert: str) -> None:
        """Set the mtls cert in the relation data bag."""
        if not self.etcd_relation:
            return
        local_model = self.etcd_relation_local_model
        local_model.requests[0].mtls_cert = cert
        self.etcd_interface.interface.write_model(self.etcd_relation.id, local_model)

    @override
    def update_requests_from_certs(self, certs: list[Certificate]) -> None:
        """Update the requests in the relation data bag from the assigned certificates."""
        if not self.etcd_relation:
            return
        local_model = self.etcd_relation_local_model

        request_common_names = {
            _get_common_name_from_chain(request.mtls_cert): request
            for request in local_model.requests
            if request.mtls_cert
        }

        requests_to_send = []
        for certificate in certs:
            cur_request = request_common_names.get(
                certificate.common_name,
                RequirerCommonModel(resource=f"/{certificate.common_name}/"),
            )

            cur_request.mtls_cert = certificate.raw
            requests_to_send.append(cur_request)

        local_model.requests = requests_to_send
        self.etcd_interface.interface.write_model(self.etcd_relation.id, local_model)

    @property
    def etcd_relation(self) -> ops.Relation | None:
        """Return the etcd relation if present."""
        if not hasattr(self, "etcd_interface"):
            return None
        return self.etcd_interface.relations[0] if len(self.etcd_interface.relations) else None

    @property
    def etcd_uris(self) -> str | None:
        """Return the etcd uris."""
        remote_responses = self.remote_responses
        if not remote_responses:
            return None
        remote_response = remote_responses[0]
        return remote_response.uris

    @property
    def etcd_relation_local_model(self) -> RequirerDataContractV1[RequirerCommonModel]:
        """Return the etcd relation local model."""
        if not self.etcd_relation:
            raise RuntimeError("etcd relation not found")
        return build_model(
            self.etcd_interface.interface.repository(self.etcd_relation.id),
            RequirerDataContractV1[RequirerCommonModel],
        )

    @property
    def remote_responses(self) -> list[ResourceProviderModel] | None:
        """Return the remote response model."""
        if not self.etcd_relation:
            return None

        return build_model(
            self.etcd_interface.interface.repository(
                self.etcd_relation.id, self.etcd_relation.app
            ),
            DataContractV1[ResourceProviderModel],
        ).requests

    @property
    def credentials(self) -> dict[str, str | None] | None:
        """Return the etcd credentials."""
        remote_responses = self.remote_responses
        if not remote_responses:
            return None
        remote_response = remote_responses[0]
        return {
            "username": ",".join([resp.username for resp in remote_responses if resp.username]),
            "uris": remote_response.uris if remote_response.uris else None,
            "endpoints": remote_response.endpoints,
            "version": remote_response.version,
            "tls-ca": remote_response.tls_ca if remote_response.tls_ca else None,
        }

    @property
    def client_requests(self) -> list:
        """Return the client requests for the etcd requirer interface."""
        return [
            RequirerCommonModel(
                resource=f"/{common_name}/",
                mtls_cert=self.charm.get_certificate_of_common_name(common_name) or "",
            )
            for common_name in self.charm.common_names
        ]


class EtcdRequiresV0(EtcdRequires):
    """EtcdRequires implementation for legacy relation interface."""

    def __init__(self, charm: "RequirerCharm") -> None:
        super().__init__(charm=charm)
        self.etcd_interface = EtcdRequiresV0Base(
            charm=self.charm,
            relation_name="etcd-client",
            prefix="/requirer-charm/",
            mtls_cert=self.raw_certificate,
        )

        self.charm.framework.observe(
            self.etcd_interface.on.endpoints_changed, self._on_endpoints_changed
        )
        self.charm.framework.observe(self.etcd_interface.on.etcd_ready, self._on_resource_created)

    @override
    def _on_endpoints_changed(self, event: DatabaseEndpointsChangedEvent) -> None:
        """Handle etcd client relation data changed event."""
        logger.info("Endpoints changed: %s", event.endpoints)
        if not event.endpoints:
            logger.error("No endpoints available")
            return

    @override
    def _on_resource_created(self, event: EtcdReadyEvent) -> None:
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

    @override
    def update_mtls_certs(self, cert: str) -> None:
        """Set the mtls cert in the relation data bag."""
        if not self.etcd_relation:
            return
        self.etcd_interface.set_mtls_cert(self.etcd_relation.id, cert)

    @override
    def update_requests_from_certs(self, certs: list[Certificate]) -> None:
        """Update the requests in the relation data bag from the assigned certificates."""
        if not self.etcd_relation:
            return

        self.etcd_interface.set_mtls_cert(self.etcd_relation.id, certs[0].raw)

    @property
    def etcd_relation(self) -> ops.Relation | None:
        """Return the etcd relation if present."""
        if not hasattr(self, "etcd_interface"):
            return None
        return self.etcd_interface.relations[0] if len(self.etcd_interface.relations) else None

    @property
    def etcd_uris(self) -> str | None:
        """Return the etcd uris."""
        if not self.etcd_relation:
            return None
        return self.etcd_interface.fetch_relation_field(self.etcd_relation.id, "uris")

    @property
    def credentials(self) -> dict[str, str | None] | None:
        """Return the etcd credentials."""
        if not self.etcd_relation:
            return None

        return {
            "username": self.etcd_interface.fetch_relation_field(
                self.etcd_relation.id, "username"
            ),
            "uris": self.etcd_interface.fetch_relation_field(self.etcd_relation.id, "uris"),
            "endpoints": self.etcd_interface.fetch_relation_field(
                self.etcd_relation.id, "endpoints"
            ),
            "version": self.etcd_interface.fetch_relation_field(self.etcd_relation.id, "version"),
            "tls-ca": self.etcd_interface.fetch_relation_field(self.etcd_relation.id, "tls-ca"),
        }

    @property
    def raw_certificate(self) -> str:
        """Return the raw certificate."""
        certs, _ = self.charm.certificates.get_assigned_certificates()
        if not certs:
            return ""
        return certs[0].ca.raw if self.charm.send_ca_option else certs[0].certificate.raw


def _get_common_name_from_chain(mtls_cert: str) -> str:
    """Get common name from chain."""
    raw_cas = mtls_cert.split("-----END CERTIFICATE-----")
    raw_cas.remove("")
    # add the marker back to the certificate
    # we take the first certificate from the provided mtls_cert, assuming this is the client cert
    cert = raw_cas[0].strip() + "\n-----END CERTIFICATE-----"
    return Certificate.from_string(cert).common_name
