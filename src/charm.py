#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed machine operator for etcd."""

import logging
from subprocess import CalledProcessError

import charm_refresh
import ops
import ops.log
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from data_platform_helpers.advanced_statuses.handler import StatusHandler

from common.exceptions import HealthCheckFailedError
from core.cluster import ClusterState
from events.backup import BackupEvents
from events.etcd import EtcdEvents
from events.external_clients import ExternalClientsEvents
from events.refresh import MachinesEtcdRefresh
from events.tls import TLSEvents
from literals import (
    METRICS_PORT,
    RESTART_RELATION,
    SUBSTRATE,
    TLSCARotationState,
    TLSState,
    TLSType,
)
from managers.backup import BackupManager
from managers.cluster import ClusterManager
from managers.config import ConfigManager
from managers.external_clients import ExternalClientsManager
from managers.tls import TLSManager
from managers.upgrades import UpgradesManager
from statuses import EtcdServiceStatuses
from workload import EtcdWorkload

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class EtcdOperatorCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, *args):
        super().__init__(*args)
        # Show logger name (module name) in logs
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, ops.log.JujuLogHandler):
                handler.setFormatter(logging.Formatter("{name}:{message}", style="{"))

        self.workload = EtcdWorkload()
        self.state = ClusterState(self, substrate=SUBSTRATE)

        # --- UPGRADES ---
        try:
            self.refresh = charm_refresh.Machines(
                MachinesEtcdRefresh(workload_name="etcd", charm_name="charmed-etcd", charm=self)
            )
        except (charm_refresh.UnitTearingDown, charm_refresh.PeerRelationNotReady):
            self.refresh = None

        # --- MANAGERS ---
        self.cluster_manager = ClusterManager(state=self.state, workload=self.workload)
        self.config_manager = ConfigManager(
            state=self.state, workload=self.workload, config=self.config
        )
        self.tls_manager = TLSManager(self.state, self.workload, SUBSTRATE)
        self.backup_manager = BackupManager(state=self.state, workload=self.workload)
        self.external_clients_manager = ExternalClientsManager(
            self.state, self.workload, SUBSTRATE
        )
        self.upgrades_manager = UpgradesManager(workload=self.workload, refresh=self.refresh)

        # --- STATUS HANDLER ---
        self.status = StatusHandler(  # priority order
            self,
            self.upgrades_manager,
            self.cluster_manager,
            self.config_manager,
            self.tls_manager,
            self.external_clients_manager,
            self.backup_manager,
        )

        # --- EVENT HANDLERS ---
        self.etcd_events = EtcdEvents(self)
        self.tls_events = TLSEvents(self)
        self.backup_events = BackupEvents(self)
        self.external_clients_events = ExternalClientsEvents(self)

        # --- LIB EVENT HANDLERS ---
        self.restart = RollingOpsManager(self, relation=RESTART_RELATION, callback=self._restart)

        # cos agent
        self._grafana_agent = COSAgentProvider(
            self,
            metrics_rules_dir="./src/cos/alert_rules/prometheus",
            dashboard_dirs=["./src/cos/grafana_dashboards"],
            scrape_configs=[
                {
                    "job_name": "etcd",
                    "static_configs": [
                        {"targets": [f"{self.state.unit_server.ip}:{METRICS_PORT}"]}
                    ],
                }
            ],
        )

        if self.refresh and not self.refresh.next_unit_allowed_to_refresh:
            self._post_snap_refresh()

    def _post_snap_refresh(self) -> None:
        """Handle post-snap refresh health checks and set next_unit_allowed_to_refresh."""
        if not self.refresh.in_progress:
            self.refresh.next_unit_allowed_to_refresh = True
            self.state.statuses.delete(
                EtcdServiceStatuses.SERVICE_NOT_RUNNING.value,
                scope="unit",
                component=self.cluster_manager.name,
            )
            return

        logger.info("Restarting workload after snap refresh")
        self.workload.restart()
        if not self.cluster_manager.is_healthy():
            return

        self.refresh.next_unit_allowed_to_refresh = True
        self.state.statuses.delete(
            EtcdServiceStatuses.SERVICE_NOT_RUNNING.value,
            scope="unit",
            component=self.cluster_manager.name,
        )

    def _restart(self, _) -> None:
        """Restart callback for the rolling ips lib."""
        logger.debug("executing normal rolling restart")
        logger.debug(f"relation data for unit: {self.state.unit_server.relation_data}")

        self.config_manager.set_config_properties()
        if not self.cluster_manager.restart_member():
            raise HealthCheckFailedError("Failed to check health of the member after restart")

    def rolling_restart(self, callback_override: str = "_restart") -> None:
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
            try:
                self.tls_manager.check_certificate_validity(tls_type=TLSType.CLIENT)
                raise HealthCheckFailedError("Failed to check health of the member after restart")
            except CalledProcessError:
                logger.warning("Health check failed, TLS client certificates expired")

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
            try:
                self.tls_manager.check_certificate_validity(tls_type=TLSType.CLIENT)
                raise HealthCheckFailedError("Failed to check health of the member after restart")
            except CalledProcessError:
                logger.warning("Health check failed, TLS client certificates expired")

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
            try:
                self.tls_manager.check_certificate_validity(tls_type=TLSType.CLIENT)
                raise HealthCheckFailedError("Failed to check health of the member after restart")
            except CalledProcessError:
                logger.warning("Health check failed, TLS client certificates expired")

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
            try:
                self.tls_manager.check_certificate_validity(tls_type=TLSType.CLIENT)
                raise HealthCheckFailedError("Failed to check health of the member after restart")
            except CalledProcessError:
                logger.warning("Health check failed, TLS client certificates expired")

    def _restart_ca_rotation(self, _) -> None:
        """Restart callback for CA rotation."""
        logger.debug("ca rotation restart")

        self.config_manager.set_config_properties()
        # do not raise in case health check fails
        # this can happen if the client certificate has already expired
        # on CA-rotation the certs are only updated AFTER all cluster members updated the CA
        if not self.cluster_manager.restart_member():
            try:
                self.tls_manager.check_certificate_validity(tls_type=TLSType.CLIENT)
                raise HealthCheckFailedError("Failed to check health of the member after restart")
            except CalledProcessError:
                logger.warning("Health check failed, TLS client certificates expired")

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

        # peer CA rotation
        if self.state.unit_server.tls_peer_ca_rotation_state == TLSCARotationState.CERT_UPDATED:
            self.tls_manager.update_cas([self.tls_manager.collect_peer_ca()], TLSType.PEER)
            self.tls_manager.set_ca_rotation_state(TLSType.PEER, TLSCARotationState.NO_ROTATION)

        # client CA rotation
        if self.state.unit_server.tls_client_ca_rotation_state == TLSCARotationState.CERT_UPDATED:
            self.tls_manager.update_cas(self.tls_manager.collect_client_cas(), TLSType.CLIENT)
            self.tls_manager.set_ca_rotation_state(TLSType.CLIENT, TLSCARotationState.NO_ROTATION)

        self.config_manager.set_config_properties()
        # do not raise in case health check fails
        # this can happen if the client certificate has already expired but was not renewed yet
        # in case the peer certificate came first
        if not self.cluster_manager.restart_member():
            try:
                self.tls_manager.check_certificate_validity(tls_type=TLSType.CLIENT)
                raise HealthCheckFailedError("Failed to check health of the member after restart")
            except CalledProcessError:
                logger.warning("Health check failed, TLS client certificates expired")


if __name__ == "__main__":  # pragma: nocover
    ops.main(EtcdOperatorCharm)  # type: ignore
