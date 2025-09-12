#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all cluster/quorum/rbac related tasks."""

import logging
from json import JSONDecodeError
from typing import List

from data_platform_helpers.advanced_statuses.models import StatusObject
from data_platform_helpers.advanced_statuses.protocol import ManagerStatusProtocol
from data_platform_helpers.advanced_statuses.types import Scope
from ops import BlockedStatus
from requests import RequestException
from tenacity import Retrying, retry, stop_after_attempt, wait_fixed, wait_random_exponential

from common.client import EtcdClient
from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdClusterManagementError,
    EtcdServiceError,
    EtcdUserManagementError,
    RaftLeaderNotFoundError,
)
from core.cluster import ClusterState
from core.models import Member
from core.workload import WorkloadBase
from literals import CLIENT_PORT, INTERNAL_USER, METRICS_PORT, EtcdClusterState, TLSState
from statuses import CharmStatuses, ClusterStatuses, EtcdServiceStatuses

logger = logging.getLogger(__name__)


class ClusterManager(ManagerStatusProtocol):
    """Manage cluster members, quorum and authorization."""

    name: str = "cluster"
    state: ClusterState

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload
        self.admin_user = INTERNAL_USER
        self.admin_password = self.state.cluster.internal_user_credentials.get(INTERNAL_USER, "")
        self.cluster_endpoints = [server.client_url for server in self.state.servers]

    @property
    def leader(self) -> str:
        """Query the etcd cluster for the raft leader.

        Returns:
            str: The member id of the raft leader in hex representation.
        """
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=self.state.unit_server.client_url,
            )
            endpoint_status = client.get_endpoint_status()
            leader_id = endpoint_status["Status"]["leader"]
            # the leader ID is returned as int, but needs to be processed as hex
            # e.g. ID=4477466968462020105 needs to be returned as 3e23287c34b94e09
            return hex(leader_id)[2:]
        except (KeyError, JSONDecodeError) as e:
            raise RaftLeaderNotFoundError(f"No raft leader found: {e}")

    @property
    def is_cluster_failed(self) -> bool:
        """Check if the cluster is experiencing majority failure.

        Returns:
            bool: True if the cluster has failed, False if not.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=f"http://{self.state.unit_server.ip}:{METRICS_PORT}/metrics",
        )

        if client.get_metric(metric_name="etcd_server_has_leader") == "0":
            logger.warning("Cluster failed - no raft leader")
            return True

        return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        reraise=True,
    )
    def enable_authentication(self) -> None:
        """Enable the etcd admin user and authentication."""
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=self.state.unit_server.client_url,
            )
            client.add_user(username=self.admin_user)
            client.enable_auth()
        except (EtcdAuthNotEnabledError, EtcdUserManagementError):
            raise

    def update_credentials(self, username: str, password: str) -> None:
        """Update a user's password.

        Args:
            username (str): The username to update.
            password (str): The new password.
        """
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=self.state.unit_server.client_url,
            )
            client.update_password(username=username, new_password=password)
        except EtcdUserManagementError:
            raise

    @property
    def member(self) -> Member:
        """Get the member information of the current unit.

        Returns:
            Member: The member object.
        """
        logger.debug(f"Getting member for unit {self.state.unit_server.member_name}")
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

        member_list = client.member_list()
        if member_list is None:
            raise ValueError("member list command failed")
        if self.state.unit_server.member_name not in member_list:
            raise ValueError("member name not found")

        logger.debug(f"Member: {member_list[self.state.unit_server.member_name].id}")
        return member_list[self.state.unit_server.member_name]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        reraise=True,
    )
    def broadcast_peer_url(self, peer_urls: str) -> None:
        """Broadcast the peer URL to all units in the cluster.

        Args:
            peer_urls (str): The peer URLs to broadcast.
        """
        logger.debug(
            f"Broadcasting peer URL: {peer_urls} for unit {self.state.unit_server.member_name}"
        )
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client.broadcast_peer_url(self.member.id, peer_urls)

    def is_healthy(self, cluster: bool = True) -> bool:
        """Run the `endpoint health` command and return True if healthy.

        Args:
            cluster (bool): True if the health check should be cluster-wide.

        Returns:
                bool: True if the cluster or node is healthy.
        """
        if not self.admin_password:
            self.admin_password = self.state.cluster.internal_user_credentials.get(
                INTERNAL_USER, ""
            )

        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.is_healthy(cluster=cluster)

    def restart_member(self, move_leader: bool = True) -> bool:
        """Restart the workload.

        Returns:
            bool: True if the workload is running after restart.
        """
        if move_leader:
            self.move_leader_if_required()

        logger.debug("Restarting workload")
        self.workload.restart()
        return self.is_healthy(cluster=False)

    def add_member(self, unit_name: str) -> None:
        """Add a new member to the etcd cluster."""
        # retrieve the member information for the newly joined unit from the set of EtcdServers
        server = next(iter([s for s in self.state.servers if s.unit_name == unit_name]), None)
        if not server:
            raise KeyError(f"Peer relation data for unit {unit_name} not found.")

        # we need to make sure all required information are available before adding the member
        if server.member_name and server.ip and server.peer_url:
            peer_url = server.peer_url
            # When the peer relation joined event is triggered, the peer_url is in http:// format
            # because the node would still not have gotten its certificates
            if self.state.unit_server.tls_peer_state == TLSState.TLS:
                peer_url = peer_url.replace("http://", "https://")
            try:
                client = EtcdClient(
                    username=self.admin_user,
                    password=self.admin_password,
                    client_url=self.state.unit_server.client_url,
                )
                cluster_members, member_id = client.add_member_as_learner(
                    server.member_name, peer_url
                )
                self.state.cluster.update(
                    {"cluster_members": cluster_members, "learning_member": member_id}
                )
                logger.info(f"Added unit {unit_name} as new cluster member {member_id}.")
            except (EtcdClusterManagementError, JSONDecodeError):
                raise
        else:
            raise KeyError(f"Peer relation data for unit {unit_name} not found.")

    def start_member(self) -> None:
        """Start a cluster member and update its status."""
        self.workload.start()
        if not self.workload.is_reachable(self.state.unit_server.ip, CLIENT_PORT):
            raise EtcdServiceError("Etcd service failed to start")
        # this triggers a relation_changed event which the leader will use to promote
        # a learner-member to fully-voting member
        self.state.unit_server.update({"state": "started"})
        if not self.state.cluster.cluster_state:
            # mark the cluster as initialized
            self.state.cluster.update(
                {
                    "cluster_state": EtcdClusterState.EXISTING.value,
                    "cluster_members": self.state.unit_server.member_endpoint,
                }
            )

    def promote_learning_member(self) -> None:
        """Promote a learning member to full-voting member."""
        member_id = self.state.cluster.learning_member

        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=",".join(e for e in self.cluster_endpoints),
            )
            client.promote_member(member_id=member_id)
        except EtcdClusterManagementError:
            raise

        self.state.cluster.update({"learning_member": ""})
        logger.info(f"Successfully promoted learning member {member_id}.")

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_random_exponential(multiplier=2, max=60),
        reraise=True,
    )
    def remove_member(self) -> None:
        """Remove a cluster member and stop the workload."""
        self.move_leader_if_required()
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=",".join(e for e in self.cluster_endpoints),
            )
            client.remove_member(self.member.id)
        except (EtcdClusterManagementError, RaftLeaderNotFoundError):
            raise
        except ValueError:
            # the unit is not a cluster member anymore, we just move on
            return

    def select_new_leader(self) -> str:
        """Choose a new leader from the current cluster members.

        Returns:
            str: The member id of the next cluster member in hex representation.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

        member_list = client.member_list()
        if member_list is None:
            raise ValueError("member list command failed")
        member_list.pop(self.state.unit_server.member_name, None)
        return next(iter(member_list.values())).id

    def move_leader_if_required(self) -> None:
        """Move the raft leadership of the cluster to the next available member if required."""
        try:
            if self.member.id == self.leader:
                new_leader_id = self.select_new_leader()
                logger.debug(f"Next selected leader: {new_leader_id}")

                client = EtcdClient(
                    username=self.admin_user,
                    password=self.admin_password,
                    client_url=",".join(e for e in self.cluster_endpoints),
                )
                client.move_leader(new_leader_id)
                # wait for leadership to be moved before continuing operation
                if self.is_healthy(cluster=True):
                    logger.debug(f"Successfully moved leader to {new_leader_id}.")
        except (
            EtcdClusterManagementError,
            RaftLeaderNotFoundError,
            ValueError,
            StopIteration,
        ) as e:
            logger.warning(f"Could not transfer cluster leadership: {e}")
            return

    def remove_inconsistent_members_if_required(self) -> None:
        """Check current cluster members and remove those not existing anymore."""
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

        cluster_members = client.member_list()
        for name, member in cluster_members.items():
            if name not in [server.member_name for server in self.state.servers]:
                logger.warning(
                    f"Member {name} does not exist anymore and will be removed from the cluster."
                )
                client.remove_member(member.id)

        self.update_cluster_member_state()

    def update_cluster_member_state(self) -> None:
        """Get up-to-date member information and store in cluster state."""
        if not self.state.cluster.cluster_state:
            return

        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

        try:
            member_list = client.member_list()
            cluster_members = ",".join(f"{k}={v.peer_urls[0]}" for k, v in member_list.items())
            self.state.cluster.update({"cluster_members": cluster_members})
        except Exception as e:
            # we should not have errors here, but if we do, we don't want the error to raise
            logger.warning(f"Error updating the cluster member state: {e}")

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:  # noqa: C901
        """Compute the Cluster manager's statuses."""
        status_list: list[StatusObject] = self.state.statuses.get(
            scope=scope, component=self.name, running_status_only=True, running_status_type="async"
        ).root

        if self.state.unit_server.unit.status == BlockedStatus(
            EtcdServiceStatuses.SERVICE_NOT_INSTALLED.value.message
        ):
            return [EtcdServiceStatuses.SERVICE_NOT_INSTALLED.value]

        if not self.state.peer_relation:
            status_list.append(EtcdServiceStatuses.SERVICE_INSTALLING.value)
            return status_list

        if self.state.unit_server.is_started:
            if (
                self.state.cluster.cluster_state != EtcdClusterState.EXISTING.value
                and not self.state.cluster.is_restore_in_progress
                and not self.state.cluster.rebuild_cluster_in_progress
            ):
                status_list.append(ClusterStatuses.CLUSTER_NOT_INITIALIZED.value)

            if not self.state.cluster.auth_enabled:
                status_list.append(ClusterStatuses.AUTHENTICATION_NOT_ENABLED.value)

            try:
                if self.is_cluster_failed:
                    status_list.append(ClusterStatuses.CLUSTER_FAILED.value)
            except RequestException as e:
                logger.warning(f"Could not determine if cluster failed: {e}")

        if not self.state.cluster.cluster_state:
            status_list.append(ClusterStatuses.CLUSTER_INITIALIZING.value)

        if self.state.unit_server.member_endpoint not in self.state.cluster.cluster_members:
            if self.state.unit_server.tls_peer_state in [TLSState.TO_TLS, TLSState.TO_NO_TLS]:
                status_list.append(ClusterStatuses.CLUSTER_MEMBER_RECONFIGURATION.value)
            else:
                status_list.append(ClusterStatuses.CLUSTER_NOT_JOINED.value)

        if self.state.cluster.rebuild_cluster_in_progress:
            status_list.append(ClusterStatuses.CLUSTER_REBUILD_IN_PROGRESS.value)

        if self.state.cluster.learning_member and self.state.unit_server.is_juju_leader:
            status_list.append(ClusterStatuses.CLUSTER_MEMBER_NOT_PROMOTED.value)

        return status_list if status_list else [CharmStatuses.ACTIVE_IDLE.value]

    def get_user(self, username: str) -> dict | None:
        """Get the user information.

        Args:
            username (str): The username to get.

        Returns:
            (dict | None): The user information or None if the user does not exist.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.get_user(username=username)

    def get_version(self) -> str:
        """Get the etcd version.

        Returns:
            str: The etcd version.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.get_version()

    def remove_managed_user(self, username: str) -> None:
        """Remove user and role from the cluster.

        Args:
            username (str): The name of the user to remove.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

        client.remove_role(username)
        client.remove_user(username)
        logger.info(f"Removed managed user {username}")

    def add_managed_user(self, username: str, keys_prefix: str) -> None:
        """Add user and role to the cluster.

        Args:
            username (str): The name of the user to add.
            keys_prefix (str): The keys prefix to grant permission to.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client.add_user(username)
        client.add_role(username)
        client.grant_role(username, username)
        client.grant_permission(username, keys_prefix)
        logger.info(f"Added managed user {username}")

    def list_users(self) -> List[str]:
        """List all users in the cluster.

        Returns:
            List[str]: The list of users.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.list_users()

    def clean_users(self) -> None:
        """Clean up users that errored on deletion."""
        etcd_users = set(self.list_users())
        etcd_users.discard(INTERNAL_USER)
        active_users = set(self.state.cluster.managed_users.values())

        for inactive_user in etcd_users - active_users:
            try:
                for attempt in Retrying(
                    stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True
                ):
                    with attempt:
                        logger.debug(
                            f"Removing inactive user {inactive_user} from etcd - attempt {attempt.retry_state.attempt_number}"
                        )
                        self.remove_managed_user(inactive_user)
            except EtcdUserManagementError as e:
                logger.error(f"Failed to remove inactive user from etcd: {e}")
