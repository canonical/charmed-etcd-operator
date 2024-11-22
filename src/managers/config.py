#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling configuration building + writing."""

import logging
from pathlib import Path

import yaml
from ops.model import ConfigData

from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import CONFIG_FILE

logger = logging.getLogger(__name__)

WORKING_DIR = Path(__file__).absolute().parent


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
    def config_properties(self) -> str:
        """Assemble the config properties.

        Returns:
            List of properties to be written to the config file.
        """
        with open(f"{WORKING_DIR}/config/etcd.conf.yml") as config:
            config_properties = yaml.safe_load(config)

        config_properties["name"] = self.state.unit_server.member_name
        config_properties["initial-advertise-peer-urls"] = self.state.unit_server.peer_url
        config_properties["initial-cluster-state"] = self.state.cluster.initial_cluster_state
        config_properties["listen-peer-urls"] = self.state.unit_server.peer_url
        config_properties["listen-client-urls"] = self.state.unit_server.client_url
        config_properties["advertise-client-urls"] = self.state.unit_server.client_url
        config_properties["initial-cluster"] = self._get_cluster_endpoints()

        return yaml.safe_dump(config_properties)

    def set_config_properties(self) -> None:
        """Write the config properties to the config file."""
        self.workload.write_file(
            content=self.config_properties,
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
