#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all cluster/quorum/rbac related tasks."""

import logging
import socket

from common.client import EtcdClient
from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdUserManagementError,
    RaftLeaderNotFoundError,
)
from core.cluster import ClusterState
from literals import INTERNAL_USER

logger = logging.getLogger(__name__)


class ClusterManager:
    """Manage cluster members, quorum and authorization."""

    def __init__(self, state: ClusterState):
        self.state = state
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

    def get_member_id(self) -> str:
        """Get the member ID of the current unit."""
        logger.debug(f"Getting member ID for unit {self.state.unit_server.member_name}")
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

        logger.debug(f"Member ID: {member_list[self.state.unit_server.member_name].id}")
        return member_list[self.state.unit_server.member_name].id

    def broadcast_peer_url(self, peer_urls: str) -> None:
        """Broadcast the peer URL to all units in the cluster."""
        logger.debug(
            f"Broadcasting peer URL: {peer_urls} for unit {self.state.unit_server.member_name}"
        )
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client._run_etcdctl(
            command="member",
            subcommand="update",
            endpoints=self.state.unit_server.client_url,
            auth_username=self.admin_user,
            auth_password=self.admin_password,
            member_id=self.get_member_id(),
            peer_urls=peer_urls,
        )

    def health_check(self, cluster=True) -> bool:
        """Run the `endpoint health` command and return True if healthy."""
        if not self.admin_password:
            self.admin_password = self.state.cluster.internal_user_credentials.get(
                INTERNAL_USER, ""
            )

        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.health_check(cluster=cluster)
