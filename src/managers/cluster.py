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
            client = EtcdClient(client_url=endpoint)
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
                logger.warning("No raft leader found in cluster.")

        return None


class EtcdClient:
    """Handle etcd client connections and run etcdctl commands."""

    def __init__(
        self,
        client_url: str,
    ):
        self.client_url = client_url

    def get_endpoint_status(self) -> dict:
        """Run the `endpoint status` command and return the result as dict."""
        endpoint_status = {}
        if result := self._run_etcdctl(
            command="endpoint",
            subcommand="status",
            endpoints=self.client_url,
            output_format="json",
        ):
            endpoint_status = json.loads(result)[0]

        return endpoint_status

    def _run_etcdctl(
        self,
        command: str,
        subcommand: str | None,
        endpoints: str,
        output_format: str | None = "simple",
    ) -> str | None:
        """Execute `etcdctl` command via subprocess.

        The list of arguments will be extended once authentication/encryption is implemented.
        This method aims to provide a very clear interface for executing `etcdctl` and minimize
        the margin of error on cluster operations.

        Args:
            command: command to execute with etcdctl, e.g. `elect`, `member` or `endpoint`
            subcommand: subcommand to add to the previous command, e.g. `add` or `status`
            endpoints: str-formatted list of endpoints to run the command against
            output_format: set the output format (fields, json, protobuf, simple, table)
            ...

        Returns:
            The output of the subprocess-command as a string. In case of error, this will
            return `None`. It will not raise an error in order to leave error handling up
            to the caller. Depending on what command is executed, the ways of handling errors
            might differ.
        """
        try:
            result = subprocess.run(
                args=[
                    "etcdctl",
                    command,
                    subcommand,
                    f"--endpoints={endpoints}",
                    f"-w={output_format}",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        except subprocess.CalledProcessError as e:
            logger.warning(e)
            return None

        return result
