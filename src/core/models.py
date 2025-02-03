#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of state objects for the Etcd relations, apps and units."""

import logging
from dataclasses import dataclass

from charms.data_platform_libs.v0.data_interfaces import Data, DataPeerData, DataPeerUnitData
from ops.model import Application, Relation, Unit

from literals import (
    CLIENT_PORT,
    INTERNAL_USER,
    PEER_PORT,
    SUBSTRATES,
    TLSCARotationState,
    TLSState,
)

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
            # use del instead of pop here because of error with dataplatform-libs
            try:
                del self.relation_data[field]
            except KeyError:
                pass


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
        return f"{self.unit.app.name}{self.unit_id}"

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
        scheme = "https" if self.tls_peer_state in [TLSState.TLS, TLSState.TO_NO_TLS] else "http"
        return f"{scheme}://{self.ip}:{PEER_PORT}"

    @property
    def client_url(self) -> str:
        """The client connection endpoint for the etcd server."""
        scheme = "https" if self.tls_client_state in [TLSState.TLS, TLSState.TO_NO_TLS] else "http"
        return f"{scheme}://{self.ip}:{CLIENT_PORT}"

    @property
    def tls_client_state(self) -> TLSState:
        """The current TLS state of the etcd server."""
        return TLSState(self.relation_data.get("tls_client_state", TLSState.NO_TLS.value))

    @property
    def tls_peer_state(self) -> TLSState:
        """The current TLS state of the etcd server."""
        return TLSState(self.relation_data.get("tls_peer_state", TLSState.NO_TLS.value))

    @property
    def peer_cert_ready(self) -> bool:
        """Check if the peer certificate is ready."""
        return self.relation_data.get("peer_cert_ready", "") == "True"

    @property
    def client_cert_ready(self) -> bool:
        """Check if the client certificate is ready."""
        return self.relation_data.get("client_cert_ready", "") == "True"

    @property
    def certs_ready(self) -> bool:
        """Check if all certificates are ready."""
        return self.peer_cert_ready and self.client_cert_ready

    @property
    def member_endpoint(self) -> str:
        """Concatenate member_name and peer_url."""
        return f"{self.member_name}={self.peer_url}"

    @property
    def is_started(self) -> bool:
        """Check if the unit has started."""
        return self.relation_data.get("state", "") == "started"

    @property
    def tls_peer_ca_rotation_state(self) -> TLSCARotationState:
        """Check if the peer CA rotation is enabled."""
        return TLSCARotationState(
            self.relation_data.get("tls_peer_ca_rotation", TLSCARotationState.NO_ROTATION.value)
        )

    @property
    def tls_client_ca_rotation_state(self) -> TLSCARotationState:
        """Check if the client CA rotation is enabled."""
        return TLSCARotationState(
            self.relation_data.get("tls_client_ca_rotation", TLSCARotationState.NO_ROTATION.value)
        )


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
    def cluster_state(self) -> str:
        """The cluster state ('new' or 'existing') of the etcd cluster."""
        return self.relation_data.get("cluster_state", "")

    @property
    def internal_user_credentials(self) -> dict[str, str]:
        """Retrieve the credentials for the internal admin user."""
        if password := self.relation_data.get(f"{INTERNAL_USER}-password"):
            return {INTERNAL_USER: password}

        return {}

    @property
    def auth_enabled(self) -> bool:
        """Flag to check if authentication is already enabled in the Cluster."""
        return self.relation_data.get("authentication", "") == "enabled"

    @property
    def cluster_members(self) -> str:
        """Get the list of current members added to the etcd cluster.

        This string is the output of the `etcdctl member add` command issued by the juju leader
        when a new unit joins and is added as cluster member. This string needs to be provided
        as an argument `--initial-cluster` when starting the workload on the newly added unit.

        This data is added to the peer cluster relation app databag when the first unit initializes
        the cluster on startup after deployment.
        """
        return self.relation_data.get("cluster_members", "")

    @property
    def learning_member(self) -> str:
        """Get the current learning member.

        New cluster members are added to the etcd cluster as so-called learning members. That means
        they are not participating in raft leader election because they do not yet have up-to-data
        data. When added as cluster members with the `add member` command, the juju leader will
        put the unit's `member_id` here. After promoting to full voting member, the juju leader
        will unset the `member_id` here.
        """
        return self.relation_data.get("learning_member", "")


@dataclass
class Member:
    """Class representing the members of an ETCD cluster."""

    id: str
    name: str
    peer_urls: list[str]
    client_urls: list[str]
