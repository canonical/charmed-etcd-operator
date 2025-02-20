#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling external clients."""

import json
import logging
from pathlib import Path

from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import SUBSTRATES

logger = logging.getLogger(__name__)

WORKING_DIR = Path(__file__).absolute().parent


class ExternalClientsManager:
    """Handle the external clients related logic."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
        substrate: SUBSTRATES,
    ):
        self.state = state
        self.workload = workload
        self.substrate = substrate

    def update_managed_user(self, relation_id: int, common_name: str):
        """Update the user."""
        managed_users = self.state.cluster.managed_users
        managed_users[relation_id] = common_name

        self.state.cluster.update(
            {
                "managed_users": json.dumps(managed_users),
            }
        )

    def remove_managed_user(self, relation_id: int):
        """Remove the user."""
        managed_users = self.state.cluster.managed_users
        del managed_users[relation_id]
        self.state.cluster.update(
            {
                "managed_users": json.dumps(managed_users),
            }
        )

    def add_managed_user(self, relation_id: int, common_name: str):
        """Add the user."""
        managed_users = self.state.cluster.managed_users
        managed_users[relation_id] = common_name

        self.state.cluster.update(
            {
                "managed_users": json.dumps(managed_users),
            }
        )
