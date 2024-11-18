#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling configuration building + writing."""

import logging

from ops.model import ConfigData

from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import CONFIG_FILE

logger = logging.getLogger(__name__)

DEFAULT_PROPERTIES = """
initial-cluster-token: 'etcd-cluster'
snapshot-count: 10000
heartbeat-interval: 100
election-timeout: 1000
quota-backend-bytes: 0
max-snapshots: 5
max-wals: 5
strict-reconfig-check: false
enable-pprof: true
proxy: 'off'
proxy-failure-wait: 5000
proxy-refresh-interval: 30000
proxy-dial-timeout: 1000
proxy-write-timeout: 5000
proxy-read-timeout: 0
force-new-cluster: false
auto-compaction-mode: periodic
auto-compaction-retention: "1"
"""

# these config properties are not used at the moment
# they are only listed here for completeness
TLS_PROPERTIES = """
client-transport-security:
  cert-file:
  # Path to the client server TLS key file.
  key-file:
  client-cert-auth: false
  trusted-ca-file:
  auto-tls: false
peer-transport-security:
  cert-file:
  key-file:
  client-cert-auth: false
  trusted-ca-file:
  auto-tls: false
  allowed-cn:
  allowed-hostname:
cipher-suites: [
  TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
  TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384
]
tls-min-version: 'TLS1.2'
tls-max-version: 'TLS1.3'
"""


class ConfigManager:
    """Handle the configuration of etcd."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
        config: ConfigData,
    ):
        self.state = state
        self.workload = workload
        self.config = config
        self.config_file = CONFIG_FILE

    @property
    def config_properties(self) -> list[str]:
        """Assemble the config properties.

        Returns:
            List of properties to be written to the config file.
        """
        properties = [
            f"name: {self.state.unit_server.member_name}",
            f"initial-advertise-peer-urls: {self.state.unit_server.peer_url}",
            f"initial-cluster-state: {self.state.cluster.initial_cluster_state}",
            f"listen-peer-urls: {self.state.unit_server.peer_url}",
            f"listen-client-urls: {self.state.unit_server.client_url}",
            f"advertise-client-urls: {self.state.unit_server.client_url}",
            f"initial-cluster: {self._get_cluster_endpoints()}",
        ] + DEFAULT_PROPERTIES.split("\n")

        return properties

    def set_config_properties(self) -> None:
        """Write the config properties to the config file."""
        self.workload.write_file(
            content="\n".join(self.config_properties),
            file=self.config_file,
        )

    def _get_cluster_endpoints(self) -> str:
        """Concatenate peer-urls of all cluster members.

        Returns:
            Str of member name and peer url for all cluster members in required syntax, e.g.:
            etcd1=http://10.54.237.109:2380,etcd2=http://10.54.237.57:2380
        """
        cluster_endpoints = ",".join(
            f"{server.member_name}={server.peer_url}" for server in self.state.servers
        )

        return cluster_endpoints
