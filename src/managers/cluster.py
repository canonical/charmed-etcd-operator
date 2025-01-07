#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all cluster/quorum/rbac related tasks."""

import logging
import socket

from common.client import EtcdClient
from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdClusterManagementError,
    EtcdUserManagementError,
    RaftLeaderNotFoundError,
)
from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import INTERNAL_USER

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
            Dict of string keys 'hostname', 'ip' and their values
        """
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)

        return {"hostname": hostname, "ip": ip}

    def get_leader(self) -> str | None:
        """Query the etcd cluster for the raft leader and return the client_url as string."""
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
            except KeyError:
                # for now, we don't raise an error if there is no leader
                # this may change when we have actual relevant tasks performed against the leader
                raise RaftLeaderNotFoundError("No raft leader found in cluster.")

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
        """Update a user's password."""
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=self.state.unit_server.client_url,
            )
            client.update_password(username=username, new_password=password)
        except EtcdUserManagementError:
            raise

    def add_member(self, unit_name: str) -> None:
        """Add a new member to the etcd cluster."""
        # retrieve the member information for the newly joined unit from the set of EtcdServers
        member_name = ""
        ip = ""
        peer_url = ""

        for server in self.state.servers:
            if server.unit_name == unit_name:
                member_name = server.member_name
                ip = server.ip
                peer_url = server.peer_url
                break

        # we need to make sure all required information are available before adding the member
        if member_name and ip and peer_url:
            try:
                client = EtcdClient(
                    username=self.admin_user,
                    password=self.admin_password,
                    client_url=self.state.unit_server.client_url,
                )
                cluster_members, member_id = client.add_member_as_learner(member_name, peer_url)
                self.state.cluster.update(
                    {"cluster_members": cluster_members, "learning_member": member_id}
                )
                logger.info(f"Added unit {unit_name} as new cluster member {member_id}.")
            except EtcdClusterManagementError:
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
                    "cluster_state": "existing",
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
