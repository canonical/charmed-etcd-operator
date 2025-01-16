#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all cluster/quorum/rbac related tasks."""

import logging
import socket
from json import JSONDecodeError

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
from literals import INTERNAL_USER, EtcdClusterState

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

    def get_leader(self) -> str | None:
        """Query the etcd cluster for the raft leader and return the client_url as string.

        Returns:
            str | None: The client URL of the raft leader or None if no leader is found.
        """
        # loop through list of hosts and compare their member id with the leader
        # if they match, return this host's endpoint
        for endpoint in self.cluster_endpoints:
            client = EtcdClient(
                username=self.admin_user, password=self.admin_password, client_url=endpoint
            )
            try:
                endpoint_status = client.get_endpoint_status()
                member_id = endpoint_status["Status"]["header"]["member_id"]
                leader_id = endpoint_status["Status"]["leader"]
                if member_id == leader_id:
                    leader = endpoint
                    return leader
            except (KeyError, JSONDecodeError) as e:
                # for now, we don't raise an error if there is no leader
                # this may change when we have actual relevant tasks performed against the leader
                raise RaftLeaderNotFoundError(f"No raft leader found in cluster: {e}")

        return None

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
        client.broadcast_peer_url(self.state.unit_server.client_url, self.member.id, peer_urls)

    def is_healthy(self, cluster=True) -> bool:
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

    def restart_member(self) -> bool:
        """Restart the workload.

        Returns:
            bool: True if the workload is running after restart.
        """
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
            try:
                client = EtcdClient(
                    username=self.admin_user,
                    password=self.admin_password,
                    client_url=self.state.unit_server.client_url,
                )
                cluster_members, member_id = client.add_member_as_learner(
                    server.member_name, server.peer_url
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
                client_url=self.state.unit_server.client_url,
            )
            client.promote_member(member_id=member_id)
        except EtcdClusterManagementError:
            raise

        self.state.cluster.update({"learning_member": ""})
        logger.info(f"Successfully promoted learning member {member_id}.")
