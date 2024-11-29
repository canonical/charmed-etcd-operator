#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all cluster/quorum/rbac related tasks."""

import logging
import socket

from common.client import EtcdClient
from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdUserNotCreatedError,
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
        self.admin_password = self.state.cluster.internal_user_credentials
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

    def enable_authentication(self):
        """Enable the etcd admin user and authentication."""
        try:
            endpoint = self.get_leader()
            client = EtcdClient(client_url=endpoint)
            client.add_admin_user()
            client.enable_auth()
        except (RaftLeaderNotFoundError, EtcdAuthNotEnabledError, EtcdUserNotCreatedError):
            raise
