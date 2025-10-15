#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of state objects for the Etcd relations, apps and units."""

import json
import logging
from dataclasses import dataclass
from typing import Any, final

from charms.data_platform_libs.v1.data_interfaces import (
    ExtraSecretStr,
    OpsOtherPeerUnitRepositoryInterface,
    OpsPeerRepositoryInterface,
    OpsPeerUnitRepositoryInterface,
    PeerModel,
)
from ops.model import Application, Relation, Unit
from pydantic import Field

from literals import (
    CLIENT_PORT,
    INTERNAL_USER,
    PEER_PORT,
    SUBSTRATES,
    RestoreStep,
    TLSCARotationState,
    TLSState,
)

logger = logging.getLogger(__name__)


class PeerAppModel(PeerModel):
    """Model for the peer application data."""

    cluster_state: str | None = Field(default=None)
    internal_user_credentials: ExtraSecretStr = Field(default=None)
    authentication: str | None = Field(default=None)
    # This string is the output of the `etcdctl member add` command issued by the juju leader
    # when a new unit joins and is added as cluster member. This string needs to be provided
    # as an argument `--initial-cluster` when starting the workload on the newly added unit.
    # This data is added to the peer cluster relation app databag when the first unit initializes
    # the cluster on startup after deployment.
    cluster_members: str = Field(default="")
    # New cluster members are added to the etcd cluster as so-called learning members. That means
    # they are not participating in raft leader election because they do not yet have up-to-data
    # data. When added as cluster members with the `add member` command, the juju leader will
    # put the unit's `member_id` here. After promoting to full voting member, the juju leader
    # will unset the `member_id` here.
    learning_member: str = Field(default="")
    managed_users: dict[int, str] = Field(default_factory=dict)
    s3_credentials: ExtraSecretStr = Field(default=None)
    azure_credentials: ExtraSecretStr = Field(default=None)
    backup_id: str | None = Field(default=None)
    restore_id: str | None = Field(default=None)
    restore_instruction: str | None = Field(default=None)
    restore_verification_failed: str | None = Field(default=None)
    rebuild_cluster: str | None = Field(default=None)


class PeerUnitModel(PeerModel):
    """Model for the peer unit data."""

    hostname: str | None = Field(default=None)
    private_ip: str | None = Field(default=None)
    public_ip: str | None = Field(default=None)
    tls_client_state: str | None = Field(default=None)
    tls_peer_state: str | None = Field(default=None)
    client_cert_ready: str | None = Field(default=None)
    peer_cert_ready: str | None = Field(default=None)
    tls_peer_certificates_expiring: str | None = Field(default=None)
    tls_client_certificates_expiring: str | None = Field(default=None)
    state: str | None = Field(default=None)
    rebuild_completed: str | None = Field(default=None)
    tls_peer_ca_rotation: str | None = Field(default=None)
    tls_client_ca_rotation: str | None = Field(default=None)
    restore_step: str | None = Field(default=None)


class RelationState:
    """Relation state object."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: OpsPeerRepositoryInterface[PeerAppModel]
        | OpsPeerUnitRepositoryInterface[PeerUnitModel]
        | OpsOtherPeerUnitRepositoryInterface[PeerUnitModel],
        component: Unit | Application | None,
        substrate: SUBSTRATES,
    ):
        self.relation = relation
        self.data_interface = data_interface
        self.component = component
        self.substrate = substrate

    def update(self, items: dict[str, Any]) -> None:
        """Write to relation data."""
        if not self.relation:
            logger.warning(
                f"Fields {list(items.keys())} were attempted to be written on the relation before it exists."
            )
            return

        delete_fields = [key for key in items if not items[key]]
        update_content = {k: items[k] for k in items if k not in delete_fields}

        model = self.data_interface.build_model(self.relation.id)
        for field, value in update_content.items():
            setattr(model, field.replace("-", "_"), value)

        for field in delete_fields:
            setattr(model, field.replace("-", "_"), None)

        self.data_interface.write_model(self.relation.id, model)


@final
class EtcdServer(RelationState):
    """State/Relation data collection for a unit."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: OpsPeerUnitRepositoryInterface[PeerUnitModel]
        | OpsOtherPeerUnitRepositoryInterface[PeerUnitModel],
        component: Unit,
        substrate: SUBSTRATES,
    ):
        super().__init__(relation, data_interface, component, substrate)
        self.data_interface = data_interface
        self.unit = component

    @property
    def unit_id(self) -> int:
        """The id of the unit from the unit name."""
        return int(self.unit.name.split("/")[1])

    @property
    def unit_name(self) -> str:
        """The unit's name."""
        return self.unit.name

    @property
    def member_name(self) -> str:
        """The Human-readable name for this etcd cluster member."""
        return f"{self.unit.app.name}{self.unit_id}"

    @property
    def model(self) -> PeerUnitModel | None:
        """The peer relation model for this unit."""
        return self.data_interface.build_model(self.relation.id) if self.relation else None

    @property
    def peer_url(self) -> str:
        """The peer connection endpoint for the etcd server."""
        scheme = "https" if self.tls_peer_state in [TLSState.TLS, TLSState.TO_NO_TLS] else "http"
        # `peer_url` MUST be IP address, etcd does not support using DNS names for the listeners
        # see https://github.com/etcd-io/etcd/blob/main/CHANGELOG/CHANGELOG-3.2.md#breaking-changes
        return f"{scheme}://{self.model.private_ip}:{PEER_PORT}" if self.model else ""

    @property
    def client_url(self) -> str:
        """The client connection endpoint for the etcd server."""
        scheme = "https" if self.tls_client_state in [TLSState.TLS, TLSState.TO_NO_TLS] else "http"
        # `client_url` MUST be IP address, etcd does not support using DNS names for the listeners
        # see https://github.com/etcd-io/etcd/blob/main/CHANGELOG/CHANGELOG-3.2.md#breaking-changes
        return f"{scheme}://{self.model.private_ip}:{CLIENT_PORT}" if self.model else ""

    @property
    def tls_client_state(self) -> TLSState:
        """The current TLS state of the etcd server."""
        return (
            TLSState(self.model.tls_client_state or TLSState.NO_TLS.value)
            if self.model
            else TLSState.NO_TLS
        )

    @property
    def tls_peer_state(self) -> TLSState:
        """The current TLS state of the etcd server."""
        return (
            TLSState(self.model.tls_peer_state or TLSState.NO_TLS.value)
            if self.model
            else TLSState.NO_TLS
        )

    @property
    def peer_cert_ready(self) -> bool:
        """Check if the peer certificate is ready."""
        return self.model.peer_cert_ready == "True" if self.model else False

    @property
    def client_cert_ready(self) -> bool:
        """Check if the client certificate is ready."""
        return self.model.client_cert_ready == "True" if self.model else False

    @property
    def certs_ready(self) -> bool:
        """Check if all certificates are ready."""
        return self.peer_cert_ready and self.client_cert_ready

    @property
    def tls_peer_certs_expiring(self) -> bool:
        """Check if any certificate is expiring."""
        return self.model.tls_peer_certificates_expiring == "True" if self.model else False

    @property
    def tls_client_certs_expiring(self) -> bool:
        """Check if any certificate is expiring."""
        return self.model.tls_client_certificates_expiring == "True" if self.model else False

    @property
    def member_endpoint(self) -> str:
        """Concatenate member_name and peer_url."""
        return f"{self.member_name}={self.peer_url}"

    @property
    def is_started(self) -> bool:
        """Check if the unit has started."""
        return self.model.state == "started" if self.model else False

    @property
    def rebuild_completed(self) -> bool:
        """Check if the has been processed for cluster rebuild."""
        return self.model.rebuild_completed == "True" if self.model else False

    @property
    def tls_peer_ca_rotation_state(self) -> TLSCARotationState:
        """Check if the peer CA rotation is enabled."""
        return (
            TLSCARotationState(
                self.model.tls_peer_ca_rotation or TLSCARotationState.NO_ROTATION.value
            )
            if self.model
            else TLSCARotationState.NO_ROTATION
        )

    @property
    def tls_client_ca_rotation_state(self) -> TLSCARotationState:
        """Check if the client CA rotation is enabled."""
        return (
            TLSCARotationState(
                self.model.tls_client_ca_rotation or TLSCARotationState.NO_ROTATION.value
            )
            if self.model
            else TLSCARotationState.NO_ROTATION
        )

    @property
    def restore_step(self) -> RestoreStep:
        """Get the current progress of the restore workflow."""
        return (
            RestoreStep(self.model.restore_step or RestoreStep.NOT_STARTED.value)
            if self.model
            else RestoreStep.NOT_STARTED
        )

    @property
    def is_juju_leader(self) -> bool:
        """Check if the current unit is the leader of the cluster."""
        return self.unit.is_leader()


@final
class EtcdCluster(RelationState):
    """State/Relation data collection for the etcd application."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: OpsPeerRepositoryInterface[PeerAppModel],
        component: Application,
        substrate: SUBSTRATES,
    ):
        super().__init__(relation, data_interface, component, substrate)
        self.app = component
        self.data_interface = data_interface

    @property
    def model(self) -> PeerAppModel | None:
        """The peer relation model for this application."""
        return self.data_interface.build_model(self.relation.id) if self.relation else None

    @property
    def internal_user_credentials(self) -> dict[str, str]:
        """Retrieve the credentials for the internal admin user."""
        if self.model and (password := self.model.internal_user_credentials):
            return {INTERNAL_USER: password.get_secret_value()}

        return {}

    @property
    def auth_enabled(self) -> bool:
        """Flag to check if authentication is already enabled in the Cluster."""
        return self.model.authentication == "enabled" if self.model else False

    @property
    def s3_credentials(self) -> dict[str, str]:
        """Get credentials and parameters to access s3 object storage."""
        if not self.model:
            return {}
        return json.loads(
            self.model.s3_credentials.get_secret_value() if self.model.s3_credentials else "{}"
        )

    @property
    def azure_credentials(self) -> dict[str, str]:
        """Get credentials and parameters to access azure object storage."""
        if not self.model:
            return {}
        return json.loads(
            self.model.azure_credentials.get_secret_value()
            if self.model.azure_credentials
            else "{}"
        )

    @property
    def is_backup_in_progress(self) -> bool:
        """Flag to indicate if the cluster is creating a backup."""
        return bool(self.model.backup_id) if self.model else False

    @property
    def is_restore_in_progress(self) -> bool:
        """Flag to indicate if the cluster is restoring a backup."""
        return bool(self.model.restore_id) if self.model else False

    @property
    def restore_instruction(self) -> RestoreStep:
        """Current step of the restore workflow to be executed by the cluster members."""
        if not self.model or not self.model.restore_instruction:
            return RestoreStep.NOT_STARTED
        return RestoreStep(self.model.restore_instruction)

    @property
    def restore_verification_failed(self) -> bool:
        """Flag for failed restore verification."""
        return bool(self.model.restore_verification_failed) if self.model else False

    @property
    def rebuild_cluster_in_progress(self) -> bool:
        """Flag to indicate if the cluster is being rebuilt to recover from majority failure."""
        return bool(self.model.rebuild_cluster) if self.model else False


@dataclass
class Member:
    """Class representing the members of an ETCD cluster."""

    id: str
    name: str
    peer_urls: list[str]
    client_urls: list[str]
