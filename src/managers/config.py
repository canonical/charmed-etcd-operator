#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling configuration building + writing."""

import logging
from pathlib import Path

import yaml
from data_platform_helpers.advanced_statuses.models import StatusObject
from data_platform_helpers.advanced_statuses.protocol import ManagerStatusProtocol
from data_platform_helpers.advanced_statuses.types import Scope
from ops.model import ConfigData

from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import (
    CONFIG_FILE,
    DATABASE_DIR,
    METRICS_PORT,
    PRODUCTION_QUOTA_BACKEND_BYTES,
    QUOTA_BACKEND_BYTES,
    Profile,
    RestoreStep,
    TLSState,
    TuningOptions,
)
from statuses import CharmStatuses, ConfigStatuses

logger = logging.getLogger(__name__)

WORKING_DIR = Path(__file__).absolute().parent


class ConfigManager(ManagerStatusProtocol):
    """Handle the configuration of etcd."""

    name: str = "config"
    state: ClusterState

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
        config: ConfigData,
    ):
        self.state = state
        self.workload = workload
        self.config = config
        self.config_file = workload.paths.config_file

    @property
    def config_properties(self) -> str:  # noqa: C901
        """Assemble the config properties.

        Returns:
            List of properties to be written to the config file.
        """
        with open(f"{WORKING_DIR}/config/etcd.conf.yml") as config:
            # load the config properties provided from the template in this repo
            # it does NOT load the template from disk in the charm unit
            # this is in order to avoid config drift
            config_properties = yaml.safe_load(config)

        config_properties["name"] = self.state.unit_server.member_name
        if (
            self.state.cluster.is_restore_in_progress
            and self.state.cluster.restore_instruction == RestoreStep.VERIFY
        ):
            # when verifying a backup restore, cluster configuration is only the local unit
            config_properties["initial-cluster-state"] = self.state.cluster.cluster_state
            config_properties["initial-cluster"] = self.state.unit_server.member_endpoint
        elif self.state.cluster.cluster_state:
            # regular situation: cluster is initialized and cluster configuration should be applied
            config_properties["initial-cluster-state"] = self.state.cluster.cluster_state
            config_properties["initial-cluster"] = self.state.cluster.cluster_members
        elif self.workload.exists(DATABASE_DIR):
            # if no cluster state is available, but we find a database file
            # we force a new one-cluster-member with existing data
            # this is the case for storage reuse and `rebuild_cluster` workflows
            config_properties["force-new-cluster"] = True
        else:
            config_properties["initial-cluster-state"] = "new"
            # on very first cluster initialization, only the leader should be cluster member
            # all other units will be added to the cluster subsequently
            config_properties["initial-cluster"] = self.state.unit_server.member_endpoint
        config_properties["initial-advertise-peer-urls"] = self.state.unit_server.peer_url
        config_properties["listen-peer-urls"] = self.state.unit_server.peer_url
        config_properties["listen-client-urls"] = self.state.unit_server.client_url
        config_properties["advertise-client-urls"] = self.state.unit_server.client_url
        config_properties["listen-metrics-urls"] = (
            f"http://{self.state.unit_server.ip}:{METRICS_PORT}"
        )

        if self.are_tuning_parameters_valid():
            for option in TuningOptions:
                # take over the config value set by users
                config_properties[option.value] = self.config.get(option.value)

            config_profile = Profile(self.config.get("profile"))
            if config_profile == Profile.PRODUCTION:
                config_properties[QUOTA_BACKEND_BYTES] = PRODUCTION_QUOTA_BACKEND_BYTES
            else:
                config_properties.pop(QUOTA_BACKEND_BYTES, None)

        if self.state.unit_server.tls_client_state in [TLSState.TO_TLS, TLSState.TLS]:
            # replace http with https in listen-client-urls and advertise-client-urls
            config_properties["listen-client-urls"] = self.state.unit_server.client_url.replace(
                "http://", "https://"
            )
            config_properties["advertise-client-urls"] = self.state.unit_server.client_url.replace(
                "http://", "https://"
            )
            # set the client-transport-security
            config_properties["client-transport-security"] = {
                "cert-file": self.workload.paths.tls.client_cert,
                "key-file": self.workload.paths.tls.client_key,
                "client-cert-auth": True,
                "trusted-ca-file": self.workload.paths.tls.client_ca,
            }
        if self.state.unit_server.tls_peer_state in [TLSState.TO_TLS, TLSState.TLS]:
            # replace http with https in listen-peer-urls, initial-cluster and initial-advertise-peer-urls
            config_properties["listen-peer-urls"] = self.state.unit_server.peer_url.replace(
                "http://", "https://"
            )
            config_properties["initial-cluster"] = config_properties["initial-cluster"].replace(
                self.state.unit_server.peer_url,
                self.state.unit_server.peer_url.replace("http://", "https://"),
            )
            config_properties["initial-advertise-peer-urls"] = config_properties[
                "initial-advertise-peer-urls"
            ].replace("http://", "https://")

            # set the peer-transport-security
            config_properties["peer-transport-security"] = {
                "cert-file": self.workload.paths.tls.peer_cert,
                "key-file": self.workload.paths.tls.peer_key,
                "client-cert-auth": True,
                "trusted-ca-file": self.workload.paths.tls.peer_ca,
            }

        if self.state.unit_server.tls_client_state == TLSState.TO_NO_TLS:
            config_properties["listen-client-urls"] = self.state.unit_server.client_url.replace(
                "https://", "http://"
            )
            config_properties["advertise-client-urls"] = self.state.unit_server.client_url.replace(
                "https://", "http://"
            )

        if self.state.unit_server.tls_peer_state == TLSState.TO_NO_TLS:
            config_properties["listen-peer-urls"] = self.state.unit_server.peer_url.replace(
                "https://", "http://"
            )
            config_properties["initial-cluster"] = config_properties["initial-cluster"].replace(
                "https://", "http://"
            )
            config_properties["initial-advertise-peer-urls"] = config_properties[
                "initial-advertise-peer-urls"
            ].replace("https://", "http://")

        return yaml.safe_dump(config_properties)

    def set_config_properties(self) -> None:
        """Write the config properties to the config file."""
        logger.debug("Writing configuration")
        self.workload.write_file(
            content=self.config_properties,
            file=self.config_file,
        )

    def are_tuning_parameters_valid(self) -> bool:
        """Validate configuration values for tuning parameters and profile.

        Returns:
            bool: True if tuning config values are valid, False if invalid.
        """
        if heartbeat_interval := self.config.get(TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value):
            if heartbeat_interval < 10 or heartbeat_interval > 5000:
                logger.error(f"Invalid heartbeat_interval value: {heartbeat_interval}")
                return False

        if election_timeout := self.config.get(TuningOptions.ELECTION_TIMEOUT_CONFIG.value):
            if election_timeout < heartbeat_interval * 10 or election_timeout > 50000:
                logger.error(f"Invalid election_timeout value: {election_timeout}")
                return False

        if profile := self.config.get("profile"):
            if profile not in [option.value for option in Profile]:
                logger.error(f"Invalid profile value: {profile}")
                return False

        return True

    def requires_restart(self) -> bool:
        """Check current configuration and determine if restart is required."""
        try:
            current_config_values = self.workload.load_yaml_file(CONFIG_FILE)
        except yaml.YAMLError as e:
            logger.error(f"Error loading current config: {e}")
            return False

        for option in TuningOptions:
            if self.config.get(option.value) != current_config_values[option.value]:
                logger.info(f"Config change to {option.value} requires restart")
                return True

        config_profile = Profile(self.config.get("profile"))
        if (
            config_profile == Profile.PRODUCTION
            and current_config_values.get(QUOTA_BACKEND_BYTES) != PRODUCTION_QUOTA_BACKEND_BYTES
        ) or (config_profile == Profile.TESTING and QUOTA_BACKEND_BYTES in current_config_values):
            logger.info("Config change to profile requires restart")
            return True

        return False

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the Cluster manager's statuses."""
        status_list: list[StatusObject] = []

        if self.config.get("profile") not in [option.value for option in Profile]:
            status_list.append(ConfigStatuses.PROFILE_INVALID.value)

        if not self.are_tuning_parameters_valid():
            status_list.append(ConfigStatuses.TUNING_CONFIG_INVALID.value)

        return status_list if status_list else [CharmStatuses.ACTIVE_IDLE.value]

    def _get_cluster_endpoints(self) -> str:
        """Concatenate peer-urls of all cluster members.

        Returns:
            str: Member name and peer url for all cluster members in required syntax, e.g.:
            etcd1=http://10.54.237.109:2380,etcd2=http://10.54.237.57:2380
        """
        cluster_endpoints = ",".join(
            f"{server.member_name}={server.peer_url}" for server in self.state.servers
        )

        return cluster_endpoints
