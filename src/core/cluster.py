#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Objects representing the state of EtcdOperatorCharm."""

import logging
from typing import TYPE_CHECKING, Dict, Set

from charms.data_platform_libs.v0.data_interfaces import (
    DataPeerData,
    DataPeerOtherUnitData,
    DataPeerUnitData,
)
from ops import Object, Relation, Unit

from core.models import EtcdCluster, EtcdServer
from literals import PEER_RELATION, SUBSTRATES

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class ClusterState(Object):
    """Global state object for the etcd cluster."""

    def __init__(self, charm: "EtcdOperatorCharm", substrate: SUBSTRATES):
        super().__init__(parent=charm, key="charm_state")
        self.substrate: SUBSTRATES = substrate
        self.peer_app_interface = DataPeerData(self.model, relation_name=PEER_RELATION)
        self.peer_unit_interface = DataPeerUnitData(self.model, relation_name=PEER_RELATION)

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
    def peer_units_data_interfaces(self) -> Dict[Unit, DataPeerOtherUnitData]:
        """Get unit data interface of all peer units from the cluster peer relation."""
        if not self.peer_relation or not self.peer_relation.units:
            return {}

        return {
            unit: DataPeerOtherUnitData(model=self.model, unit=unit, relation_name=PEER_RELATION)
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
