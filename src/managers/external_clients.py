#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling external clients."""

import json
import logging
from pathlib import Path

from charms.tls_certificates_interface.v4.tls_certificates import Certificate
from data_platform_helpers.advanced_statuses.models import StatusObject
from data_platform_helpers.advanced_statuses.protocol import ManagerStatusProtocol
from data_platform_helpers.advanced_statuses.types import Scope

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

    def update_client_relations_data(self, etcd_version: str) -> None:
        """Update the ECR data."""
        if not self.state.etcd_provides.relations:
            return

        if not self.state.cluster.cluster_state:
            logger.debug("Cluster not yet initialized, cannot update client relation data.")
            return

        cluster_server_names = {
            uri.split("=")[0] for uri in self.state.cluster.cluster_members.split(",")
        }
        cluster_servers = {
            server
            for server in self.state.servers
            if server.member_name in cluster_server_names
            and server.tls_client_state == TLSState.TLS
        }

        uris = {server.client_url for server in cluster_servers}
        endpoints = {f"{server.ip}:{CLIENT_PORT}" for server in cluster_servers}

        server_ca = self.state.tls_client_certificate.ca.raw

        for relation in self.state.etcd_provides.relations:
            if not self.state.etcd_provides.fetch_relation_field(
                relation.id, "prefix"
            ) or not self.state.etcd_provides.fetch_relation_field(relation.id, "mtls-cert"):
                # Skip relations with invalid payloads
                logger.warning(f"Skipping relation {relation.id} with invalid payloads.")
                continue

            relation_data = self.state.etcd_provides.fetch_my_relation_data(
                [relation.id], ["uris", "endpoints", "tls-ca", "version"]
            )[relation.id]

            if set(relation_data.get("uris", "").split(",")) != uris:
                self.state.etcd_provides.set_uris(relation.id, ",".join(uris))

            if set(relation_data.get("endpoints", "").split(",")) != endpoints:
                self.state.etcd_provides.set_endpoints(relation.id, ",".join(endpoints))

            if relation_data.get("tls-ca") != server_ca:
                self.state.etcd_provides.set_tls_ca(relation.id, server_ca)

            if relation_data.get("version") != etcd_version:
                self.state.etcd_provides.set_version(relation.id, etcd_version)

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the component status."""
        status_list: list[StatusObject] = self.state.statuses.get(
            scope=scope, component=self.name, running_status_only=True, running_status_type="async"
        ).root

        for relation in self.state.etcd_provides.relations:
            mtls_cert = self.state.etcd_provides.fetch_relation_field(relation.id, "mtls-cert")
            prefix = self.state.etcd_provides.fetch_relation_field(relation.id, "prefix")
            # for client relation created hook
            if not mtls_cert or not prefix:
                status_list.append(ExternalClientsStatuses.EC_MISSING_CREDENTIALS.value)
                continue
            if not is_leaf_certificate_valid(mtls_cert):
                status_list.append(ExternalClientsStatuses.EC_INVALID_CERTIFICATE.value)

            common_name = self.get_common_name_from_chain(mtls_cert)
            relation_managed_user = self.get_relation_managed_user(relation.id)
            if relation_managed_user and relation_managed_user != common_name:
                status_list.append(ExternalClientsStatuses.EC_USERNAME_EXISTS.value)

        if self.state.etcd_provides.relations:
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
