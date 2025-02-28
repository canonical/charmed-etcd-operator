#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all cluster/quorum/rbac related tasks."""

import logging
import socket
from json import JSONDecodeError

from tenacity import retry, stop_after_attempt, wait_random_exponential

from common.client import EtcdClient
from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdClusterManagementError,
    EtcdUserManagementError,
    RaftLeaderNotFoundError,
)
from core.cluster import ClusterState
from core.models import Member
from core.workload import WorkloadBase
from literals import INTERNAL_USER, EtcdClusterState, TLSState

logger = logging.getLogger(__name__)


class ClusterManager:
    """Manage cluster members, quorum and authorization."""

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload
        self.admin_user = INTERNAL_USER
        self.admin_password = self.state.cluster.internal_user_credentials.get(INTERNAL_USER, "")
        self.cluster_endpoints = [server.client_url for server in self.state.servers]

    def get_host_mapping(self) -> dict[str, str]:
        """Collect hostname mapping for current unit.

        Returns:
            dict[str, str]: Dict of string keys 'hostname', 'ip' and their values
        """
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)

        return {"hostname": hostname, "ip": ip}

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
            client_url=",".join(e for e in self.cluster_endpoints),
        )

        member_list = client.member_list()
        if member_list is None:
            raise ValueError("member list command failed")
        if self.state.unit_server.member_name not in member_list:
            raise ValueError("member name not found")

        logger.debug(f"Member: {member_list[self.state.unit_server.member_name].id}")
        return member_list[self.state.unit_server.member_name]

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
            client_url=",".join(e for e in self.cluster_endpoints),
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
                    client_url=",".join(e for e in self.cluster_endpoints),
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
            if self.is_healthy(cluster=True):
                client.remove_member(self.member.id)
            else:
                raise EtcdClusterManagementError("Cluster not healthy.")
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
        except (EtcdClusterManagementError, RaftLeaderNotFoundError, ValueError) as e:
            logger.warning(f"Could not transfer cluster leadership: {e}")
            return

    def update_cluster_member_state(self) -> None:
        """Get up-to-date member information and store in cluster state."""
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
