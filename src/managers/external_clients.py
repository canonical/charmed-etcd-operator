#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling external clients."""

import logging
from pathlib import Path

from core.cluster import ClusterState
from core.models import ManagedUser, ManagedUsers
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

    def update_managed_user(
        self, relation_id: int, common_name: str | None = None, ca_chain: str | None = None
    ):
        """Update the user."""
        managed_users = self.state.cluster.managed_users

        user = managed_users[relation_id]
        if common_name:
            user.common_name = common_name
        if ca_chain:
            user.ca_chain = ca_chain

        self.state.cluster.update(
            {
                "managed_users": ManagedUsers(managed_users=managed_users).model_dump_json(),
            }
        )

    def remove_managed_user(self, relation_id: int):
        """Remove the user."""
        managed_users = self.state.cluster.managed_users

        if relation_id not in managed_users:
            logger.error(f"Relation {relation_id} not found in managed users")
            return

        del managed_users[relation_id]

        self.state.cluster.update(
            {
                "managed_users": ManagedUsers(managed_users=managed_users).model_dump_json(),
            }
        )

    def add_managed_user(self, relation_id: int, common_name: str, ca_chain: str):
        """Add the user."""
        managed_users = self.state.cluster.managed_users

        managed_users[relation_id] = ManagedUser(
            relation_id=relation_id,
            common_name=common_name,
            ca_chain=ca_chain,
        )

        self.state.cluster.update(
            {
                "managed_users": ManagedUsers(managed_users=managed_users).model_dump_json(),
            }
        )
