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
from literals import (
    RESTART_RELATION,
    SUBSTRATE,
    DebugLevel,
    Status,
    TLSCARotationState,
    TLSState,
    TLSType,
)
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
        self.cluster_manager = ClusterManager(state=self.state, workload=self.workload)
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
        logger.debug("executing normal rolling restart")
        logger.debug(f"relation data for unit: {self.state.unit_server.relation_data}")

        self.config_manager.set_config_properties()
        if not self.cluster_manager.restart_member():
            self.set_status(Status.HEALTH_CHECK_FAILED)
            raise HealthCheckFailedError("Failed to check health of the member after restart")

    def rolling_restart(self, callback_override: str | None = None) -> None:
        """Initiate a rolling restart."""
        logger.info(
            f"Initiating a rolling restart in unit {self.unit.name} with callback {callback_override}"
        )
        self.on[RESTART_RELATION].acquire_lock.emit(callback_override=callback_override)

    def _restart_enable_client_tls(self, _) -> None:
        """Enable client TLS."""
        logger.debug("Client TLS custom callback")

        # enable peer tls if ready
        if (
            self.state.unit_server.tls_peer_state == TLSState.TO_TLS
            and self.state.unit_server.peer_cert_ready
        ):
            self.cluster_manager.broadcast_peer_url(
                self.state.unit_server.peer_url.replace("http://", "https://")
            )
            self.tls_manager.set_tls_state(state=TLSState.TLS, tls_type=TLSType.PEER)

        # enable client tls
        self.tls_manager.set_tls_state(state=TLSState.TLS, tls_type=TLSType.CLIENT)

        # write config and restart workload
        self.config_manager.set_config_properties()
        if not self.cluster_manager.restart_member():
            self.set_status(Status.TLS_CLIENT_TRANSITION_FAILED)
            raise HealthCheckFailedError("Failed to check health of the member after restart")

    def _restart_enable_peer_tls(self, _) -> None:
        """Enable peer TLS."""
        logger.debug("Peer TLS custom callback")

        # in case of peer TLS we need to move the leader before broadcasting membership updates
        self.cluster_manager.move_leader_if_required()

        # enable peer tls
        self.cluster_manager.broadcast_peer_url(
            self.state.unit_server.peer_url.replace("http://", "https://")
        )
        self.tls_manager.set_tls_state(state=TLSState.TLS, tls_type=TLSType.PEER)

        # enable client tls if ready
        if (
            self.state.unit_server.tls_client_state == TLSState.TO_TLS
            and self.state.unit_server.client_cert_ready
        ):
            self.tls_manager.set_tls_state(state=TLSState.TLS, tls_type=TLSType.CLIENT)

        # write config and restart workload
        self.config_manager.set_config_properties()
        if not self.cluster_manager.restart_member(move_leader=False):
            self.set_status(Status.TLS_PEER_TRANSITION_FAILED)
            raise HealthCheckFailedError("Failed to check health of the member after restart")

    def _restart_disable_client_tls(self, _) -> None:
        """Disable client TLS."""
        logger.debug("Client TLS custom callback")
        if self.state.unit_server.tls_client_state == TLSState.NO_TLS:
            logger.debug("Client TLS already disabled, skipping")
            return

        # disable peer tls if ready
        if self.state.unit_server.tls_peer_state == TLSState.TO_NO_TLS:
            logger.debug("Disabling peer TLS")
            self.cluster_manager.broadcast_peer_url(
                self.state.unit_server.peer_url.replace("https://", "http://")
            )
            self.tls_manager.delete_certificates(TLSType.PEER)
            self.tls_manager.set_tls_state(state=TLSState.NO_TLS, tls_type=TLSType.PEER)

        # disable client tls
        self.tls_manager.delete_certificates(TLSType.CLIENT)
        self.tls_manager.set_tls_state(state=TLSState.NO_TLS, tls_type=TLSType.CLIENT)

        # write config and restart workload
        self.config_manager.set_config_properties()
        if not self.cluster_manager.restart_member(move_leader=False):
            self.set_status(Status.TLS_CLIENT_TRANSITION_FAILED)
            raise HealthCheckFailedError("Failed to check health of the member after restart")

    def _restart_disable_peer_tls(self, _) -> None:
        """Disable peer TLS."""
        logger.debug("Disable Peer TLS custom callback")

        # in case of peer TLS we need to move the leader before broadcasting membership updates
        self.cluster_manager.move_leader_if_required()

        logger.debug("Peer TLS custom callback")
        if self.state.unit_server.tls_peer_state == TLSState.NO_TLS:
            logger.debug("Peer TLS already disabled, skipping")
            return

        # disable peer tls
        self.cluster_manager.broadcast_peer_url(
            self.state.unit_server.peer_url.replace("https://", "http://")
        )
        self.tls_manager.delete_certificates(TLSType.PEER)
        self.tls_manager.set_tls_state(state=TLSState.NO_TLS, tls_type=TLSType.PEER)

        # disable client tls if ready
        if self.state.unit_server.tls_client_state == TLSState.TO_NO_TLS:
            self.tls_manager.delete_certificates(TLSType.CLIENT)
            self.tls_manager.set_tls_state(state=TLSState.NO_TLS, tls_type=TLSType.CLIENT)

        # write config and restart workload
        self.config_manager.set_config_properties()
        if not self.cluster_manager.restart_member(move_leader=False):
            self.set_status(Status.TLS_PEER_TRANSITION_FAILED)
            raise HealthCheckFailedError("Failed to check health of the member after restart")

    def _restart_ca_rotation(self, _) -> None:
        """Restart callback for CA rotation."""
        logger.debug("ca rotation restart")
        self._restart(None)
        if self.state.unit_server.tls_peer_ca_rotation_state == TLSCARotationState.NEW_CA_DETECTED:
            self.tls_manager.set_ca_rotation_state(TLSType.PEER, TLSCARotationState.NEW_CA_ADDED)

        if (
            self.state.unit_server.tls_client_ca_rotation_state
            == TLSCARotationState.NEW_CA_DETECTED
        ):
            self.tls_manager.set_ca_rotation_state(TLSType.CLIENT, TLSCARotationState.NEW_CA_ADDED)

    def _restart_clean_cas(self, _) -> None:
        """Restart callback for cleaning up old CAs."""
        logger.debug("cleaning up old CAs")
        if self.state.unit_server.tls_peer_ca_rotation_state == TLSCARotationState.CERT_UPDATED:
            self.tls_manager.clean_cas(TLSType.PEER)
            self.tls_manager.set_ca_rotation_state(TLSType.PEER, TLSCARotationState.NO_ROTATION)

        if self.state.unit_server.tls_client_ca_rotation_state == TLSCARotationState.CERT_UPDATED:
            self.tls_manager.clean_cas(TLSType.CLIENT)
            self.tls_manager.set_ca_rotation_state(TLSType.CLIENT, TLSCARotationState.NO_ROTATION)

        self._restart(None)


if __name__ == "__main__":  # pragma: nocover
    ops.main(EtcdOperatorCharm)  # type: ignore
