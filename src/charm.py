#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed machine operator for etcd."""

import logging

import ops
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops import StatusBase

from common.exceptions import HealthCheckFailedError
from core.cluster import ClusterState
from events.etcd import EtcdEvents
from events.tls import TLSEvents
from literals import RESTART_RELATION, SUBSTRATE, DebugLevel, Status, TLSState
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
        self.cluster_manager = ClusterManager(self.state, self.workload)
        self.config_manager = ConfigManager(
            state=self.state, workload=self.workload, config=self.config
        )
        self.tls_manager = TLSManager(self.state, self.workload, SUBSTRATE)

        # --- EVENT HANDLERS ---
        self.etcd_events = EtcdEvents(self)
        self.tls_events = TLSEvents(self)

        # --- LIB EVENT HANDLERS ---
        self.restart = RollingOpsManager(self, relation=RESTART_RELATION, callback=self._restart)

    def set_status(self, key: Status) -> None:
        """Set charm status."""
        status: StatusBase = key.value.status
        log_level: DebugLevel = key.value.log_level

        getattr(logger, log_level.lower())(status.message)
        self.unit.status = status

    def _restart(self, _) -> None:
        """Restart callback for the rolling ips lib."""
        if self.state.unit_server.tls_state == TLSState.TO_TLS:
            try:
                logger.debug("Enabling TLS through rolling restart")
                self.cluster_manager.broadcast_peer_url(
                    self.state.unit_server.peer_url.replace("http://", "https://")
                )
                self.config_manager.set_config_properties()

                self.tls_manager.set_tls_state(state=TLSState.TLS)
                if not self.cluster_manager.restart_member():
                    logger.error("Failed to check health of the member after restart")
                    self.set_status(Status.TLS_TRANSITION_FAILED)

            except Exception as e:
                logger.error(f"Enabling TLS failed: {e}")
                self.set_status(Status.TLS_TRANSITION_FAILED)

        elif self.state.unit_server.tls_state == TLSState.TO_NO_TLS:
            try:
                logger.debug("Disabling TLS through rolling restart")
                self.cluster_manager.broadcast_peer_url(
                    self.state.unit_server.peer_url.replace("https://", "http://")
                )
                self.tls_manager.delete_certificates()
                self.config_manager.set_config_properties()
                self.tls_manager.set_tls_state(state=TLSState.NO_TLS)
                if not self.cluster_manager.restart_member():
                    logger.error("Failed to check health of the member after restart")
                    self.set_status(Status.TLS_TRANSITION_FAILED)

            except Exception as e:
                logger.error(f"Disabling TLS failed: {e}")
                self.set_status(Status.TLS_TRANSITION_FAILED)

        else:
            logger.debug("Restarting workload")
            if not self.cluster_manager.restart_member():
                raise HealthCheckFailedError("Failed to check health of the member after restart")

    def rolling_restart(self) -> None:
        """Initiate a rolling restart."""
        logger.info(f"Initiating a rolling restart unit {self.unit.name}")
        self.on[RESTART_RELATION].acquire_lock.emit()


if __name__ == "__main__":  # pragma: nocover
    ops.main(EtcdOperatorCharm)  # type: ignore
