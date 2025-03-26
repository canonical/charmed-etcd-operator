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
from ops.charm import ActionEvent, RelationChangedEvent

from common.exceptions import EtcdBackupError
from literals import (
    INTERNAL_USER_PASSWORD_CONFIG,
    PEER_RELATION,
    S3_RELATION_NAME,
    RestoreStep,
    Status,
)

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
        self.framework.observe(self.charm.on.restore_action, self._on_restore_action)
        # When the leader unit is being removed, s3_requirer.on.credentials_gone is performed on it (and only on it).
        # After a new leader is elected, the S3 connection must be reinitialized.
        self.framework.observe(self.charm.on.leader_elected, self._on_s3_credentials_changed)
        # The restore-workflow is synchronized across all units via the peer relation databag
        # see for more information: https://github.com/canonical/charmed-etcd-operator/wiki/Backup-Restore:-Workflow
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_changed, self._on_peer_relation_changed
        )

    def _on_s3_credentials_changed(self, event: CredentialsChangedEvent) -> None:
        """Handle an update of the s3 credentials from s3-integrator."""
        if not (s3_parameters := self.s3_requirer.get_s3_connection_info()):
            logger.debug(f"No relation {S3_RELATION_NAME}")
            return

        # the TLS chain needs to be stored on all units in case of Juju leadership changes
        self.charm.backup_manager.store_tls_ca_chain(s3_parameters)

        if not self.charm.unit.is_leader():
            return

        if not self.charm.state.peer_relation:
            self.charm.set_status(Status.NO_PEER_RELATION)
            event.defer()
            return

        # make sure we have all required parameters for writing to the storage
        required_parameters = ["bucket", "endpoint", "path", "access-key", "secret-key"]
        if missing_parameters := [p for p in required_parameters if p not in s3_parameters]:
            raise KeyError(f"Parameters missing from S3 integrator: {missing_parameters}")

        # Strip whitespaces from all parameters
        for key, value in s3_parameters.items():
            if isinstance(value, str):
                s3_parameters[key] = value.strip()

        # Clean up extra slash symbols to avoid issues on 3rd-party storages
        s3_parameters["endpoint"] = s3_parameters["endpoint"].rstrip("/")
        s3_parameters["path"] = s3_parameters["path"].strip("/")
        s3_parameters["bucket"] = s3_parameters["bucket"].strip("/")

        self.charm.backup_manager.create_bucket(s3_parameters)
        self.charm.state.cluster.update({"s3-credentials": json.dumps(s3_parameters)})

    def _on_s3_credentials_gone(self, event: CredentialsGoneEvent) -> None:
        """Handle the removal of the relation with s3-integrator."""
        self.charm.workload.remove_file(self.charm.workload.paths.tls.backup_ca)

        if self.charm.unit.is_leader():
            self.charm.state.cluster.update({"s3-credentials": ""})

    def _on_create_backup_action(self, event: ActionEvent) -> None:
        """Create a backup and upload to object storage."""
        if error := self._exists_preventing_reason():
            event.set_results({"error": error})
            event.fail(error)
            return

        try:
            backup_id = self.charm.backup_manager.create_backup()
        except EtcdBackupError as e:
            event.set_results({"error": e})
            event.fail(e)
            return

        event.set_results({"backup-id": backup_id})

    def _on_list_backups_action(self, event: ActionEvent) -> None:
        """List all created backups."""
        if error := self._exists_preventing_reason():
            event.set_results({"error": error})
            event.fail(error)
            return

        backup_list = self.charm.backup_manager.list_backups()
        logger.debug(f"backup list: {backup_list}")

        event.set_results({"backups": self.charm.backup_manager.format_backup_list(backup_list)})

    def _on_restore_action(self, event: ActionEvent) -> None:
        """Download a backup from object storage and restore it to all units."""
        if error := self._exists_preventing_reason(check_restore=True):
            event.set_results({"error": error})
            event.fail(error)
            return

        if not (backup_id_to_restore := event.params.get("backup-id", "")):
            event.fail("Must provide backup-id to restore.")
            return

        event.log(f"Initiating restore process for backup-id {backup_id_to_restore}")

        if not self.charm.backup_manager.download_backup_file(backup_id_to_restore):
            event.fail(f"Could not download backup-file {backup_id_to_restore}.")
            return

        # initiate synced workflow on all other units by updating peer-relation app-data
        self.charm.state.cluster.update(
            {"restore_id": backup_id_to_restore, "restore_instruction": RestoreStep.DOWNLOAD.value}
        )

        event.set_results({"success": f"restore initiated for {backup_id_to_restore}"})

    def _on_peer_relation_changed(self, event: RelationChangedEvent) -> None:
        """Synchronize restore workflow across all units."""
        if not self.charm.state.cluster.is_restore_in_progress:
            return

        match (
            # compare the current restore instruction against the current restore progress
            self.charm.state.cluster.restore_instruction,
            self.charm.state.unit_server.restore_step
        ):
            case RestoreStep.DOWNLOAD, RestoreStep.NOT_STARTED:
                self.charm.backup_manager.download_backup_file(self.charm.state.cluster.restore_id)
            case RestoreStep.STOP, RestoreStep.DOWNLOAD:
                # disable the service to avoid restart while the backup is restored
                self.charm.workload.disable_service()
                self.charm.workload.stop()
            case RestoreStep.RESTORE, RestoreStep.STOP:
                # todo: add logic for restoring
                pass
            case RestoreStep.RESTART, RestoreStep.RESTORE:
                # todo: add logic for starting the workload again
                pass
            case RestoreStep.COMPLETED, RestoreStep.RESTART:
                # todo: add logic for cleaning up databag
                pass

    def _exists_preventing_reason(self, check_restore: bool = False) -> str:
        """Check if an action can be executed, if not return error message.

        Args:
            check_restore: option to check if preconditions for restoring process are given

        Returns:
            Error message in case a preventing reason for an action exists, otherwise empty str.
        """
        if not self.charm.unit.is_leader():
            return "Action must be performed on the leader unit."

        if not self.charm.state.cluster.s3_credentials:
            return "No credentials for object storage available."

        if not self.charm.state.unit_server.is_started:
            return "Database is not started, cannot perform backup action."

        # default checks end here, the following checks are only relevant for the restore process
        if not check_restore:
            return ""

        if self.charm.state.cluster.is_restore_in_progress:
            return "Restore is already in progress."

        if not self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
            return (
                "Admin secret missing - configure `system-users` secret before restoring a backup."
            )

        return ""
