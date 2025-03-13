#!/usr/bin/env python3
# Copyright 2025 Canonical Limited
# See LICENSE file for licensing details.

"""Event handlers for creating and restoring backups."""

import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v0.s3 import (
    CredentialsChangedEvent,
    CredentialsGoneEvent,
    S3Requirer,
)
from ops import Object

from literals import S3_RELATION_NAME

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
        pass

    def _on_s3_credentials_gone(self, event: CredentialsGoneEvent):
        pass
