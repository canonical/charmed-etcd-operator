#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Objects representing the state of EtcdOperatorCharm."""

import logging
from typing import TYPE_CHECKING, Dict, Set

from charms.data_platform_libs.v1.data_interfaces import (
    DataContractV1,
    OpsOtherPeerUnitRepositoryInterface,
    OpsPeerRepositoryInterface,
    OpsPeerUnitRepositoryInterface,
    OpsRelationRepositoryInterface,
    RequirerCommonModel,
    RequirerDataContractV1,
    ResourceProviderModel,
    build_model,
)
from charms.tls_certificates_interface.v4.tls_certificates import (
    ProviderCertificate,
)
from data_platform_helpers.advanced_statuses.protocol import StatusesState, StatusesStateProtocol
from ops import ModelError, Object, Relation, SecretNotFoundError, Unit

from core.models import EtcdCluster, EtcdServer, PeerAppModel, PeerUnitModel
from literals import (
    AZURE_RELATION_NAME,
    CLIENT_TLS_RELATION_NAME,
    EXTERNAL_CLIENTS_RELATION,
    PEER_RELATION,
    PEER_TLS_RELATION_NAME,
    S3_RELATION_NAME,
    STATUS_PEERS_RELATION,
    SUBSTRATES,
)

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class ClusterState(Object, StatusesStateProtocol):
    """Global state object for the etcd cluster."""

    def __init__(self, charm: "EtcdOperatorCharm", substrate: SUBSTRATES):
        super().__init__(parent=charm, key="charm_state")
        self.charm = charm
        self.substrate: SUBSTRATES = substrate
        self.peer_app_interface = OpsPeerRepositoryInterface(
            charm, relation_name=PEER_RELATION, model=PeerAppModel
        )
        self.peer_unit_interface = OpsPeerUnitRepositoryInterface(
            charm, relation_name=PEER_RELATION, model=PeerUnitModel
        )
        self.statuses_relation_name = STATUS_PEERS_RELATION
        self.statuses = StatusesState(self, self.statuses_relation_name)
        self.config = charm.config

    @property
    def peer_relation(self) -> Relation | None:
        """Get the cluster peer relation."""
        return self.model.get_relation(PEER_RELATION)

    @property
    def unit_server(self) -> EtcdServer:
        """Get the server state of this unit."""
        return EtcdServer(
            relation=self.peer_relation,
            data_interface=self.peer_unit_interface,
            component=self.model.unit,
            substrate=self.substrate,
        )

    @property
    def peer_units_data_interfaces(
        self,
    ) -> Dict[Unit, OpsOtherPeerUnitRepositoryInterface[PeerUnitModel]]:
        """Get unit data interface of all peer units from the cluster peer relation."""
        if not self.peer_relation or not self.peer_relation.units:
            return {}

        return {
            unit: OpsOtherPeerUnitRepositoryInterface(
                charm=self.charm, relation_name=PEER_RELATION, unit=unit, model=PeerUnitModel
            )
            for unit in self.peer_relation.units
        }

    @property
    def cluster(self) -> EtcdCluster:
        """Get the cluster state of the entire etcd application."""
        return EtcdCluster(
            relation=self.peer_relation,
            data_interface=self.peer_app_interface,
            component=self.model.app,
            substrate=self.substrate,
        )

    @property
    def servers(self) -> Set[EtcdServer]:
        """Get all servers/units in the current peer relation, including this unit itself.

        Note: This is not to be confused with the list of cluster members.

        Returns:
            Set of EtcdServers with their unit data.
        """
        if not self.peer_relation:
            return set()

        servers = set()
        for unit, data_interface in self.peer_units_data_interfaces.items():
            servers.add(
                EtcdServer(
                    relation=self.peer_relation,
                    data_interface=data_interface,
                    component=unit,
                    substrate=self.substrate,
                )
            )
        servers.add(self.unit_server)

        return servers

    @property
    def peer_tls_relation(self) -> Relation | None:
        """Get the unit certificates relation."""
        return self.model.get_relation(PEER_TLS_RELATION_NAME)

    @property
    def client_tls_relation(self) -> Relation | None:
        """Get the unit certificates relation."""
        return self.model.get_relation(CLIENT_TLS_RELATION_NAME)

    @property
    def etcd_provides_interface(self) -> OpsRelationRepositoryInterface:
        """Get the etcd provides interface."""
        return OpsRelationRepositoryInterface(
            self.charm, EXTERNAL_CLIENTS_RELATION, RequirerCommonModel
        )

    @property
    def tls_client_certificate(self) -> ProviderCertificate:
        """Get the client TLS certificate."""
        return self.charm.tls_events.client_certificate.get_assigned_certificates()[0][0]

    @property
    def tls_peer_certificate(self) -> ProviderCertificate:
        """Get the peer TLS certificate."""
        return self.charm.tls_events.peer_certificate.get_assigned_certificates()[0][0]

    @property
    def tls_certificate_transfer_certificates(self) -> Set[str]:
        """Get the TLS certificates from the certificate transfer interface."""
        return self.charm.external_clients_events.certificate_transfer.get_all_certificates()

    @property
    def s3_relation(self) -> Relation | None:
        """Get the S3 integrator relation."""
        return self.model.get_relation(S3_RELATION_NAME)

    @property
    def azure_relation(self) -> Relation | None:
        """Get the Azure integrator relation."""
        return self.model.get_relation(AZURE_RELATION_NAME)

    @property
    def can_restore_workflow_proceed(self) -> bool:
        """Check if all units have completed the current restore instruction.

        This check decides if the restore workflow can continue to the next step, by comparing the
        current state of all peer units with the current restore instruction. Only if all units
        have completed the current step, the workflow may proceed.

        Returns:
            True if all units are done, False if not.
        """
        current_instruction = self.cluster.restore_instruction
        return all((unit.restore_step == current_instruction for unit in self.servers))

    def get_secret_from_id(self, secret_id: str) -> dict[str, str]:
        """Resolve the given id of a Juju secret and return the content as a dict.

        Args:
            model (Model): Model object.
            secret_id (str): The id of the secret.

        Returns:
            dict: The content of the secret.
        """
        try:
            secret_content = self.charm.model.get_secret(id=secret_id).get_content(refresh=True)
        except SecretNotFoundError:
            raise SecretNotFoundError(f"The secret '{secret_id}' does not exist.")
        except ModelError:
            raise

        return secret_content

    def get_etcd_provider_request_model(
        self, relation: Relation
    ) -> DataContractV1[ResourceProviderModel]:
        """Get the etcd provides interface."""
        return self.etcd_provides_interface.build_model(
            relation.id, DataContractV1[ResourceProviderModel]
        )

    def get_etcd_requirer_request_model(
        self, relation: Relation
    ) -> RequirerDataContractV1[RequirerCommonModel]:
        """Get the etcd requirer interface."""
        return build_model(
            self.etcd_provides_interface.repository(relation.id, relation.app),
            RequirerDataContractV1[RequirerCommonModel],
        )
