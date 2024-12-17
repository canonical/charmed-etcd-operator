#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed machine operator for etcd."""

import logging

import ops
from ops import StatusBase

from core.cluster import ClusterState
from events.etcd import EtcdEvents
from events.tls import TLSEvents
from literals import SUBSTRATE, DebugLevel, Status
from managers.cluster import ClusterManager
from managers.config import ConfigManager
from managers.tls import TLSManager
from workload import EtcdWorkload

logger = logging.getLogger(__name__)


class EtcdOperatorCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, *args):
        super().__init__(*args)
        self.workload = EtcdWorkload()
        self.state = ClusterState(self, substrate=SUBSTRATE)

        # --- MANAGERS ---
        self.cluster_manager = ClusterManager(self.state)
        self.config_manager = ConfigManager(
            state=self.state, workload=self.workload, config=self.config
        )

        # --- MANAGERS ---
        self.tls_manager = TLSManager(self.state, self.workload, SUBSTRATE)

        # --- EVENT HANDLERS ---
        self.etcd_events = EtcdEvents(self)
        self.tls_events = TLSEvents(self)

    def set_status(self, key: Status) -> None:
        """Set charm status."""
        status: StatusBase = key.value.status
        log_level: DebugLevel = key.value.log_level

        getattr(logger, log_level.lower())(status.message)
        self.unit.status = status


if __name__ == "__main__":  # pragma: nocover
    ops.main(EtcdOperatorCharm)  # type: ignore
