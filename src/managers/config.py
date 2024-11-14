#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling configuration building + writing."""

import logging

from ops.model import ConfigData

from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import CONFIG_PATH

logger = logging.getLogger(__name__)

DYNAMIC_PROPERTIES = """
data-dir: -> get from snap
wal-dir: -> get from snap
listen-peer-urls: http://localhost:2380
listen-client-urls: http://localhost:2379
initial-advertise-peer-urls: http://localhost:2380
advertise-client-urls: http://localhost:2379
initial-cluster:
initial-cluster-token: 'etcd-cluster'
initial-cluster-state: 'new'
"""

DEFAULT_PROPERTIES = """
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
        self.config_path = CONFIG_PATH

    @property
    def config_properties(self) -> list[str]:
        """Assemble the config properties.

        Returns:
            List of properties to be written to the config file.
        """
        properties = [
            f"log-level={self.config.get('log-level')}",
            f"name={self.state.unit_server.unit_name}",
        ] + DEFAULT_PROPERTIES.split("\n")

        return properties

    def set_config_properties(self) -> None:
        """Write the config properties to the config file."""
        self.workload.write(
            content="\n".join(self.config_properties),
            path=self.config_path,
        )
