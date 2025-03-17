#!/usr/bin/env python3
# Copyright 2025 Canonical Limited
# See LICENSE file for licensing details.

"""Event handlers for creating and restoring backups."""

import json
import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v0.s3 import (
    CredentialsChangedEvent,
    CredentialsGoneEvent,
    S3Requirer,
)
from ops import Object
from ops.charm import ActionEvent

from literals import S3_RELATION_NAME, Status

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class BackupEvents(Object):
    """Event handlers for creating and restoring backups."""

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, key="backup")
        self.charm = charm
        self.s3_requirer = S3Requirer(self.charm, S3_RELATION_NAME)

        self.framework.observe(
            self.s3_requirer.on.credentials_changed, self._on_s3_credentials_changed
        )
        self.framework.observe(self.s3_requirer.on.credentials_gone, self._on_s3_credentials_gone)
        self.framework.observe(self.charm.on.create_backup_action, self._on_create_backup_action)
        self.framework.observe(self.charm.on.list_backups_action, self._on_list_backups_action)

    def _on_s3_credentials_changed(self, event: CredentialsChangedEvent) -> None:
        """Handle an update of the s3 credentials from s3-integrator."""
        if not self.charm.unit.is_leader():
            return

        if not self.charm.state.peer_relation:
            self.charm.set_status(Status.NO_PEER_RELATION)
            event.defer()
            return

        # make sure we have all required parameters for writing to the storage
        required_parameters = ["bucket", "endpoint", "path", "access-key", "secret-key"]
        s3_parameters = self.s3_requirer.get_s3_connection_info()

        if missing_parameters := [p for p in required_parameters if p not in s3_parameters]:
            raise KeyError(f"Parameters missing from S3 integrator: {missing_parameters}")

        self.charm.backup_manager.create_bucket(s3_parameters)
        self.charm.state.cluster.update({"s3-credentials": json.dumps(s3_parameters)})

    def _on_s3_credentials_gone(self, event: CredentialsGoneEvent) -> None:
        """Handle the removal of the relation with s3-integrator."""
        if not self.charm.unit.is_leader():
            return

        self.charm.state.cluster.update({"s3-credentials": ""})

    def _on_create_backup_action(self, event: ActionEvent) -> None:
        """Create a backup and upload to object storage."""
        if error := self._exists_preventing_reason():
            event.set_results({"error": error})
            event.fail(error)
            return

        event.set_results({"result": "successful"})

    def _on_list_backups_action(self, event: ActionEvent) -> None:
        """List all created backups."""
        if error := self._exists_preventing_reason():
            event.set_results({"error": error})
            event.fail(error)
            return

        event.set_results({"result": "successful"})

    def _exists_preventing_reason(self) -> str:
        """Check if an action can be executed, if not return error message."""
        if not self.charm.unit.is_leader():
            return "Action must be performed on the leader unit."

        if not self.charm.state.cluster.s3_credentials:
            return "No credentials for object storage available."

        if not self.charm.state.unit_server.is_started:
            return "Database is not started, cannot perform backup action."

        return ""
