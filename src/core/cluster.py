#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Objects representing the state of EtcdOperatorCharm."""

import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v0.data_interfaces import DataPeerUnitData
from ops import Object, Relation

from core.models import EtcdServer
from literals import PEER_RELATION, SUBSTRATES

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class ClusterState(Object):
    """Global state object for the etcd cluster."""

    def __init__(self, charm: "EtcdOperatorCharm", substrate: SUBSTRATES):
        super().__init__(parent=charm, key="charm_state")
        self.substrate: SUBSTRATES = substrate
        self.peer_unit_interface = DataPeerUnitData(self.model, relation_name=PEER_RELATION)

    @property
    def peer_relation(self) -> Relation | None:
        """The cluster peer relation."""
        return self.model.get_relation(PEER_RELATION)

    @property
    def unit_server(self) -> EtcdServer:
        """The server state of the current running unit."""
        return EtcdServer(
            relation=self.peer_relation,
            data_interface=self.peer_unit_interface,
            component=self.model.unit,
            substrate=self.substrate,
        )
