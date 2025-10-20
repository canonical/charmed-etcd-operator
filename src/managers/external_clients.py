#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling external clients."""

import logging
from pathlib import Path

from charms.data_platform_libs.v1.data_interfaces import ResourceProviderModel
from charms.tls_certificates_interface.v4.tls_certificates import Certificate
from data_platform_helpers.advanced_statuses.models import StatusObject
from data_platform_helpers.advanced_statuses.protocol import ManagerStatusProtocol
from data_platform_helpers.advanced_statuses.types import Scope
from ops import Relation
from pydantic import SecretStr

from common.certificates import is_leaf_certificate_valid
from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import CLIENT_PORT, SUBSTRATES, TLSCARotationState, TLSState
from statuses import CharmStatuses, ClusterStatuses, ExternalClientsStatuses, TLSStatuses

logger = logging.getLogger(__name__)

WORKING_DIR = Path(__file__).absolute().parent


class ExternalClientsManager(ManagerStatusProtocol):
    """Handle the external clients related logic."""

    name = "external_clients"
    state: ClusterState

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
        managed_users = self.state.cluster.model.managed_users
        del managed_users[relation_id]
        self.state.cluster.update(
            {
                "managed_users": managed_users,
            }
        )

    def add_managed_user(self, relation_id: int, common_name: str) -> None:
        """Add the user.

        Args:
            relation_id (int): The relation id.
            common_name (str): The common name.
        """
        managed_users = self.state.cluster.model.managed_users
        managed_users[relation_id] = common_name

        self.state.cluster.update(
            {
                "managed_users": managed_users,
            }
        )

    def get_relation_managed_user(self, relation_id: int) -> str | None:
        """Get the relation's managed user.

        Args:
            relation_id (int): The relation id.

        Returns:
            (str): The managed user.
        """
        return (
            self.state.cluster.model.managed_users.get(relation_id)
            if self.state.cluster.model
            else None
        )

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

    def update_client_relations_data(self, etcd_version: str) -> None:
        """Update the ECR data."""
        if not self.state.etcd_provides_interface.relations:
            return

        if not self.state.cluster.model.cluster_state:
            logger.debug("Cluster not yet initialized, cannot update client relation data.")
            return

        cluster_server_names = {
            uri.split("=")[0] for uri in self.state.cluster.model.cluster_members.split(",")
        }
        cluster_servers = {
            server
            for server in self.state.servers
            if server.member_name in cluster_server_names
            and server.tls_client_state == TLSState.TLS
        }

        uris = {server.client_url for server in cluster_servers}
        endpoints = {f"{server.model.private_ip}:{CLIENT_PORT}" for server in cluster_servers}

        server_ca = self.state.tls_client_certificate.ca.raw

        for relation in self.state.etcd_provides_interface.relations:
            response_model = self.state.get_etcd_provider_request_model(relation)
            request_model = self.state.get_etcd_requirer_request_model(relation)
            for request in request_model.requests:
                if not request.resource or not request.mtls_cert:
                    logger.warning("Skipping relation %s with invalid payloads.", relation.id)
                    continue
                current_response = next(
                    (
                        res
                        for res in response_model.requests
                        if res.request_id == request.request_id
                    ),
                    None,
                )
                if not current_response:
                    logger.warning(
                        "Skipping relation %s did not find a matching response.", relation.id
                    )
                    continue

                current_response.endpoints = ",".join(endpoints)
                current_response.uris = SecretStr(",".join(uris))
                current_response.tls_ca = SecretStr(server_ca)
                current_response.version = etcd_version
            self.state.etcd_provides_interface.write_model(relation.id, response_model)

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:  # noqa: C901
        """Compute the component status."""
        status_list: list[StatusObject] = []
        for relation in self.state.etcd_provides_interface.relations:
            request_model = self.state.get_etcd_requirer_request_model(relation)
            for request in request_model.requests:
                mtls_cert = request.mtls_cert
                prefix = request.resource
                # for client relation created hook
                if not mtls_cert or not prefix:
                    status_list.append(ExternalClientsStatuses.EC_MISSING_CREDENTIALS.value)
                    continue
                if not is_leaf_certificate_valid(mtls_cert.get_secret_value()):
                    status_list.append(ExternalClientsStatuses.EC_INVALID_CERTIFICATE.value)

                # Only the leader manages the usernames
                if self.state.charm.unit.is_leader():
                    common_name = self.get_common_name_from_chain(mtls_cert.get_secret_value())
                    relation_managed_user = self.get_relation_managed_user(relation.id)
                    if relation_managed_user and relation_managed_user != common_name:
                        status_list.append(ExternalClientsStatuses.EC_USERNAME_EXISTS.value)

        if self.state.etcd_provides_interface.relations:
            if self.state.unit_server.tls_client_state in [TLSState.NO_TLS, TLSState.TO_NO_TLS]:
                status_list.append(ExternalClientsStatuses.EC_TLS_IS_DISABLED.value)

            if self.state.unit_server.tls_client_state == TLSState.TO_TLS:
                status_list.append(TLSStatuses.TLS_NOT_READY.value)

            if (
                self.state.unit_server.tls_client_ca_rotation_state
                != TLSCARotationState.NO_ROTATION
            ):
                status_list.append(TLSStatuses.TLS_CLIENT_CA_ROTATING.value)

            if not self.state.cluster.auth_enabled:
                status_list.append(ClusterStatuses.CLUSTER_NOT_INITIALIZED.value)

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]

    def get_reponse_of(self, relation: Relation, request_id: str) -> ResourceProviderModel | None:
        """Get the current response for a given request id.

        Args:
            relation (Relation): The relation.
            request_id (str): The request id.

        Returns:
            (ResourceProviderModel | None): The current response or None if not found.
        """
        if not self.state.etcd_provides_interface.relations:
            return None

        response_model = self.state.get_etcd_provider_request_model(relation)
        return next((res for res in response_model.requests if res.request_id == request_id), None)

    def get_uris(self) -> str:
        """Get the URIs of the cluster.

        Returns:
            (list[str]): The URIs of the cluster.
        """
        if not self.state.cluster.model or not self.state.cluster.model.cluster_state:
            return ""

        cluster_server_names = {
            uri.split("=")[0] for uri in self.state.cluster.model.cluster_members.split(",")
        }
        cluster_servers = {
            server
            for server in self.state.servers
            if server.member_name in cluster_server_names
            and server.tls_client_state == TLSState.TLS
        }

        return ",".join([server.client_url for server in cluster_servers])

    def get_endpoints(self) -> str:
        """Get the endpoints of the cluster.

        Returns:
            (list[str]): The endpoints of the cluster.
        """
        if not self.state.cluster.model or not self.state.cluster.model.cluster_state:
            return ""

        cluster_server_names = {
            uri.split("=")[0] for uri in self.state.cluster.model.cluster_members.split(",")
        }
        cluster_servers = {
            server
            for server in self.state.servers
            if server.member_name in cluster_server_names
            and server.tls_client_state == TLSState.TLS
        }

        return ",".join([f"{server.model.private_ip}:{CLIENT_PORT}" for server in cluster_servers])
