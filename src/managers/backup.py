#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all backup/restore related tasks."""

import logging

# from common.client import EtcdClient
from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import INTERNAL_USER

logger = logging.getLogger(__name__)


class BackupManager:
    """Manage everything related to backup and restore."""

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload
        self.admin_user = INTERNAL_USER
        self.admin_password = self.state.cluster.internal_user_credentials.get(INTERNAL_USER, "")
        self.cluster_endpoints = [server.client_url for server in self.state.servers]

    def create_bucket(self, s3_parameters: dict[str, str]) -> None:
        """Create bucket if it does not exist yet."""
        pass
