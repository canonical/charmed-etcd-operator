#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handlers for Juju Actions."""

import logging
from typing import TYPE_CHECKING

from ops.charm import ActionEvent
from ops.framework import Object

from common.exceptions import EtcdUserManagementError
from literals import INTERNAL_USER

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class ActionEvents(Object):
    """Handle all events for user-related Juju Actions."""

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, key="action_events")
        self.charm = charm

        self.framework.observe(self.charm.on.set_password_action, self._on_set_password)
        self.framework.observe(self.charm.on.get_password_action, self._on_get_password)

    def _on_get_password(self, event: ActionEvent) -> None:
        """Return the password and certificate chain for the internal admin user."""
        username = event.params.get("username")
        if username != INTERNAL_USER:
            event.fail(f"Action only allowed for user {INTERNAL_USER}.")
            return

        if not self.charm.state.cluster.internal_user_credentials:
            event.fail("User credentials not created yet.")
            return

        # todo: add the TLS CA chain here once TLS is implemented
        event.set_results(
            {
                "username": username,
                "password": self.charm.state.cluster.internal_user_credentials[INTERNAL_USER],
                "ca-chain": "...",
            }
        )

    def _on_set_password(self, event: ActionEvent) -> None:
        """Handle the `set-password` action for the internal admin user.

        If no password is provided, generate one.
        """
        username = event.params.get("username")
        if username != INTERNAL_USER:
            event.fail(f"Action only allowed for user {INTERNAL_USER}.")
            return

        if not self.charm.unit.is_leader():
            event.fail("Action can only be run on the leader unit.")
            return

        new_password = event.params.get("password") or self.charm.workload.generate_password()

        try:
            self.charm.cluster_manager.update_credentials(username=username, password=new_password)
            self.charm.state.cluster.update({f"{INTERNAL_USER}-password": new_password})
        except EtcdUserManagementError as e:
            logger.error(e)
            event.fail(e)

        event.set_results({f"{username}-password": new_password})
