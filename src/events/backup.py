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

    def _on_s3_credentials_changed(self, event: CredentialsChangedEvent):
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

    def _on_s3_credentials_gone(self, event: CredentialsGoneEvent):
        if not self.charm.unit.is_leader():
            return

        self.charm.state.cluster.update({"s3-credentials": ""})
