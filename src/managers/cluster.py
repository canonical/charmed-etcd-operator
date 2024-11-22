#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all cluster/quorum/rbac related tasks."""

import json
import logging
import socket
import subprocess

from core.cluster import ClusterState

logger = logging.getLogger(__name__)


class RaftLeaderNotFoundError(Exception):
    """Custom Exception if there is no current Raft leader."""

    pass


class ClusterManager:
    """Manage cluster members, quorum and authorization."""

    def __init__(self, state: ClusterState):
        self.state = state
        self.cluster_endpoints = [server.client_url for server in self.state.servers]
        self.leader = self.get_leader()

    def get_host_mapping(self) -> dict[str, str]:
        """Collect hostname mapping for current unit.

        Returns:
            Dict of string keys 'hostname', 'ip' and their values
        """
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)

        return {"hostname": hostname, "ip": ip}

    def get_leader(self) -> str:
        """Query the etcd cluster for the raft leader and return the client_url as string."""
        leader = ""

        # loop through list of hosts and compare their member id with the leader
        # if they match, return this host's endpoint
        for endpoint in self.cluster_endpoints:
            client = EtcdClient(client_url=endpoint)
            try:
                endpoint_status = client.get_endpoint_status()
                member_id = endpoint_status["Status"]["header"]["member_id"]
                leader_id = endpoint_status["Status"]["leader"]
                if member_id == leader_id:
                    leader = endpoint
                    logger.info(f"Raft leader found: {leader}")
                    break
            except KeyError as e:
                logger.error(f"No raft leader found in cluster: {e}")

        return leader


class EtcdClient:
    """Handle etcd client connections and run etcdctl commands."""

    def __init__(
        self,
        client_url: str,
    ):
        self.client_url = client_url

    def get_endpoint_status(self) -> dict:
        """Run the `etcdctl endpoint status` command and return the result as dict."""
        try:
            result = subprocess.check_output(
                ["etcdctl", "endpoint", "status", f"--endpoints={self.client_url}", "-w=json"]
            ).decode("UTF-8")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error: {e}")
            return {}

        endpoint_status = json.loads(result)[0]

        return endpoint_status
