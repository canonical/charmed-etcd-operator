#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of state objects for the Etcd relations, apps and units."""

import logging
from collections.abc import MutableMapping

from charms.data_platform_libs.v0.data_interfaces import Data, DataPeerData, DataPeerUnitData
from ops.model import Application, Relation, Unit

from literals import CLIENT_PORT, PEER_PORT, SUBSTRATES

logger = logging.getLogger(__name__)


class RelationState:
    """Relation state object."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: Data,
        component: Unit | Application | None,
        substrate: SUBSTRATES,
    ):
        self.relation = relation
        self.data_interface = data_interface
        self.component = component
        self.substrate = substrate
        self.relation_data = self.data_interface.as_dict(self.relation.id) if self.relation else {}

    @property
    def data(self) -> MutableMapping:
        """Data representing the state."""
        return self.relation_data

    def update(self, items: dict[str, str]) -> None:
        """Write to relation data."""
        if not self.relation:
            logger.warning(
                f"Fields {list(items.keys())} were attempted to be written on the relation before it exists."
            )
            return

        delete_fields = [key for key in items if not items[key]]
        update_content = {k: items[k] for k in items if k not in delete_fields}

        self.relation_data.update(update_content)

        for field in delete_fields:
            self.relation_data.pop(field, None)


class EtcdServer(RelationState):
    """State/Relation data collection for a unit."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerUnitData,
        component: Unit,
        substrate: SUBSTRATES,
    ):
        super().__init__(relation, data_interface, component, substrate)
        self.unit = component

    @property
    def unit_id(self) -> int:
        """The id of the unit from the unit name."""
        return int(self.unit.name.split("/")[1])

    @property
    def unit_name(self) -> str:
        """The id of the unit from the unit name."""
        return self.unit.name

    @property
    def member_name(self) -> str:
        """The Human-readable name for this etcd cluster member."""
        return f"etcd{self.unit_id}"

    @property
    def hostname(self) -> str:
        """The hostname for the unit."""
        return self.relation_data.get("hostname", "")

    @property
    def ip(self) -> str:
        """The IP address for the unit."""
        return self.relation_data.get("ip", "")

    @property
    def peer_url(self) -> str:
        """The peer connection endpoint for the etcd server."""
        return f"http://{self.ip}:{PEER_PORT}"

    @property
    def client_url(self) -> str:
        """The client connection endpoint for the etcd server."""
        return f"http://{self.ip}:{CLIENT_PORT}"


class EtcdCluster(RelationState):
    """State/Relation data collection for the etcd application."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerData,
        component: Application,
        substrate: SUBSTRATES,
    ):
        super().__init__(relation, data_interface, component, substrate)
        self.app = component

    @property
    def initial_cluster_state(self) -> str:
        """The initial cluster state ('new' or 'existing') of the etcd cluster."""
        return self.relation_data.get("initial_cluster_state", "")
