#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed machine operator for etcd."""

import dataclasses
import logging
from subprocess import CalledProcessError

import charm_refresh
import ops
import ops.log
from charms.grafana_agent.v0.cos_agent import COSAgentProvider
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops import StatusBase

from common.exceptions import HealthCheckFailedError
from core.cluster import ClusterState
from events.backup import BackupEvents
from events.etcd import EtcdEvents
from events.external_clients import ExternalClientsEvents
from events.tls import TLSEvents
from literals import (
    METRICS_PORT,
    RESTART_RELATION,
    SUBSTRATE,
    DebugLevel,
    Status,
    TLSCARotationState,
    TLSState,
    TLSType,
)
from managers.backup import BackupManager
from managers.cluster import ClusterManager
from managers.config import ConfigManager
from managers.external_clients import ExternalClientsManager
from managers.tls import TLSManager
from workload import EtcdWorkload

logger = logging.getLogger(__name__)


@dataclasses.dataclass(eq=False)
class EtcdCharmSpecific(charm_refresh.CharmSpecificMachines):
    """CharmSpecific implementation for the etcd charm."""
    
    _charm: "EtcdOperatorCharm"
    
    @classmethod
    def is_compatible(
        cls,
        *,
        old_charm_version: charm_refresh.CharmVersion,
        new_charm_version: charm_refresh.CharmVersion,
        old_workload_version: str,
        new_workload_version: str,
    ) -> bool:
        """Check if refresh from old to new versions is compatible.
        
        Args:
            old_charm_version: The charm version being refreshed from
            new_charm_version: The charm version being refreshed to
            old_workload_version: The etcd version being refreshed from
            new_workload_version: The etcd version being refreshed to
            
        Returns:
            True if the refresh is compatible, False otherwise
        """
        # Check charm version compatibility
        if not super().is_compatible(
            old_charm_version=old_charm_version,
            new_charm_version=new_charm_version,
            old_workload_version=old_workload_version,
            new_workload_version=new_workload_version,
        ):
            return False

        # Check etcd workload version compatibility
        # etcd only supports upgrades between minor versions, and does not support downgrades
        try:
            old_major, old_minor, old_patch = (
                int(component) for component in old_workload_version.split(".")
            )
            new_major, new_minor, new_patch = (
                int(component) for component in new_workload_version.split(".")
            )
        except (ValueError, IndexError):
            # If we can't parse the version, assume incompatible
            return False

        # Only allow upgrades within the same major version
        if old_major != new_major:
            return False

        # Only allow upgrades within the same minor version track
        if old_minor != new_minor:
            return False

        # Do not allow downgrades
        return new_patch >= old_patch
    
    def refresh_snap(
        self,
        *,
        snap_name: str,
        snap_revision: str,
        refresh: charm_refresh.Machines,
    ) -> None:
        """Refresh the etcd snap.
        
        Args:
            snap_name: The name of the snap to refresh
            snap_revision: The revision to refresh to
            refresh: The refresh instance for calling update_snap_revision()
        """
        from charms.operator_libs_linux.v2 import snap
        
        # 1. Gracefully stop the workload if it is running
        if self._charm.workload.alive():
            logger.info("Stopping etcd workload before snap refresh")
            self._charm.workload.stop()
        
        # 2. Refresh the workload snap
        etcd_snap = snap.SnapCache()[snap_name]
        revision_before_refresh = etcd_snap.revision
        
        assert snap_revision != revision_before_refresh
        
        try:
            logger.info(f"Refreshing snap {snap_name} from revision {revision_before_refresh} to {snap_revision}")
            etcd_snap.ensure(snap.SnapState.Present, revision=snap_revision)
            etcd_snap.hold()
        except (snap.SnapError, snap.SnapAPIError) as e:
            logger.exception("Snap refresh failed")
            if etcd_snap.revision == revision_before_refresh:
                # Refresh failed and snap revision didn't change, restart workload
                logger.info("Snap refresh failed, restarting workload")
                self._charm.workload.start()
                raise
            else:
                # Refresh succeeded but there was an error afterwards
                refresh.update_snap_revision()
                raise
        else:
            # 3. Immediately call refresh.update_snap_revision()
            refresh.update_snap_revision()
            logger.info(f"Successfully refreshed snap {snap_name} to revision {snap_revision}")
        
        # Complete the post-snap refresh workflow
        self._post_snap_refresh(refresh)
    
    def _post_snap_refresh(self, refresh: charm_refresh.Machines) -> None:
        """Handle post-snap refresh workflow: start workload, check health, allow next unit."""
        try:
            # 1. Start the workload
            logger.info("Starting etcd workload after snap refresh")
            self._charm.workload.start()
            
            # 2. Check if the application and unit are healthy
            self._ensure_application_and_unit_are_healthy()
            
            # 3. If healthy, set next_unit_allowed_to_refresh = True
            refresh.next_unit_allowed_to_refresh = True
            logger.info("Snap refresh completed successfully, next unit allowed to refresh")
            
        except Exception as e:
            logger.warning(f"Post-snap refresh health check failed: {e}")
            self._charm.unit.status = ops.BlockedStatus(f"Post-refresh health check failed: {str(e)}")
            # next_unit_allowed_to_refresh remains False, refresh will pause
    
    def _ensure_application_and_unit_are_healthy(self) -> None:
        """Check if the application and unit are healthy after refresh."""
        # Check if workload is alive
        if not self._charm.workload.alive():
            raise Exception("Workload is not running")
        
        # Check if the cluster is healthy
        if not self._charm.cluster_manager.is_healthy():
            raise Exception("Cluster is not healthy, cannot refresh")
        
        if self._charm.cluster_manager.state.cluster.rebuild_cluster_in_progress:
            raise Exception("Rebuild cluster is in progress, cannot refresh")
        
        if self._charm.cluster_manager.state.cluster.is_restore_in_progress:
            raise Exception("Restore is in progress, cannot refresh")

        if self._charm.cluster_manager.state.cluster.is_backup_in_progress:
            raise Exception("Backup is in progress, cannot refresh")

        logger.info("Application and unit health checks passed")
    
    @staticmethod
    def run_pre_refresh_checks_after_1_unit_refreshed() -> None:
        """Run pre-refresh checks after 1 unit has refreshed."""
        # TODO: Implement cross version checks on top of checks above
        pass


class EtcdOperatorCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, *args):
        super().__init__(*args)
        # Show logger name (module name) in logs
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if isinstance(handler, ops.log.JujuLogHandler):
                handler.setFormatter(logging.Formatter("{name}:{message}", style="{"))
        
        # Initialize refresh capability
        try:
            self.refresh = charm_refresh.Machines(
                EtcdCharmSpecific(
                    workload_name="etcd",
                    charm_name="charmed-etcd",
                    _charm=self,
                )
            )
        except charm_refresh.PeerRelationNotReady:
            self.unit.status = ops.MaintenanceStatus("Waiting for peer relation")
            if self.unit.is_leader():
                self.app.status = ops.MaintenanceStatus("Waiting for peer relation")
            return
        except charm_refresh.UnitTearingDown:
            self.unit.status = ops.MaintenanceStatus("Tearing down")
            # Gracefully shut down workload if needed
            return
        
        self.workload = EtcdWorkload()
        self.state = ClusterState(self, substrate=SUBSTRATE)
        self.pending_inactive_statuses: list[Status] = []

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

        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)
        self.framework.observe(self.on.collect_app_status, self._on_collect_status)
        
        # Handle post-refresh health checks if needed
        self._handle_post_refresh_health_checks()

    def _handle_post_refresh_health_checks(self) -> None:
        """Handle post-refresh health checks that need to be retried in every Juju event."""
        if not hasattr(self, 'refresh'):
            # Refresh not initialized yet (early startup or exception handling)
            return
            
        # Check if refresh is in progress and next_unit_allowed_to_refresh is not set
        if (self.refresh.in_progress and 
            not self.refresh.next_unit_allowed_to_refresh):
            
            try:
                # Retry the health checks and set next_unit_allowed_to_refresh if healthy
                logger.info("Retrying post-refresh health checks")
                
                # Ensure workload is running
                if not self.workload.alive():
                    self.workload.start()
                
                # Check if application and unit are healthy
                if self.cluster_manager.is_healthy() and self.workload.alive():
                    self.refresh.next_unit_allowed_to_refresh = True
                    logger.info("Post-refresh health checks passed, next unit allowed to refresh")
                else:
                    self.unit.status = ops.BlockedStatus("Post-refresh health check failed")
                    
            except Exception as e:
                logger.warning(f"Post-refresh health check retry failed: {e}")
                self.unit.status = ops.BlockedStatus(f"Post-refresh health check failed: {str(e)}")

    def set_status(self, key: Status) -> None:
        """Set charm status."""
        status: StatusBase = key.value.status
        log_level: DebugLevel = key.value.log_level

        getattr(logger, log_level.lower())(status.message)
        self.pending_inactive_statuses.append(key)

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
            raise HealthCheckFailedError("Failed to check health of the member after restart")

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
            self.tls_manager.update_cas([self.tls_events.collect_peer_ca()], TLSType.PEER)
            self.tls_manager.set_ca_rotation_state(TLSType.PEER, TLSCARotationState.NO_ROTATION)

        # client CA rotation
        if self.state.unit_server.tls_client_ca_rotation_state == TLSCARotationState.CERT_UPDATED:
            self.tls_manager.update_cas(self.tls_events.collect_client_cas(), TLSType.CLIENT)
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

    def _on_collect_status(self, event: ops.CollectStatusEvent) -> None:
        """Compute the current status for this unit.

        Ops framework will choose the highest-priority status and set that as the status.
        If there are multiple statuses with the same priority, the first one added wins.
        Component statuses should be computed in their respective priority.
        """
        if self.app.planned_units() == 0:
            event.add_status(Status.REMOVED.value.status)
            return

        # compute cluster status
        for status in self.cluster_manager.compute_component_status():
            event.add_status(status.value.status)

        # compute TLS status
        for status in self.tls_manager.compute_component_status():
            event.add_status(status.value.status)

        for status in self.external_clients_manager.compute_component_status():
            event.add_status(status.value.status)

        # compute backup status
        for status in self.backup_manager.compute_component_status():
            event.add_status(status.value.status)

        # add all other statuses collected during the current hook
        for status in self.pending_inactive_statuses + [Status.ACTIVE]:
            event.add_status(status.value.status)


if __name__ == "__main__":  # pragma: nocover
    ops.main(EtcdOperatorCharm)  # type: ignore
