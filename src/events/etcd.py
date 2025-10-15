#!/usr/bin/env python3
# Copyright 2024 Canonical Limited
# See LICENSE file for licensing details.

"""Etcd related and core event handlers."""

import logging
from subprocess import CalledProcessError
from typing import TYPE_CHECKING

import ops
from ops import Object
from ops.charm import (
    ActionEvent,
    LeaderElectedEvent,
    RelationChangedEvent,
    RelationCreatedEvent,
    RelationDepartedEvent,
    RelationJoinedEvent,
)
from ops.model import ModelError, SecretNotFoundError
from requests.exceptions import RequestException

from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdClusterManagementError,
    EtcdServiceError,
    EtcdUserManagementError,
    HealthCheckFailedError,
    RaftLeaderNotFoundError,
)
from literals import (
    CLIENT_PORT,
    DATA_STORAGE,
    DATABASE_DIR,
    INTERNAL_USER,
    INTERNAL_USER_PASSWORD_CONFIG,
    PEER_RELATION,
    SNAP_ARCHIVE_PATH,
    SNAP_DATA_PATH,
    SNAP_GROUP,
    SNAP_LOG_PATH,
    SNAP_USER,
    TLSCARotationState,
    TLSState,
    TLSType,
)
from statuses import ClusterStatuses, EtcdServiceStatuses

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class EtcdEvents(Object):
    """Handle all base and etcd related events."""

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, key="etcd_events")
        self.charm = charm

        # --- Core etcd charm events ---

        self.framework.observe(self.charm.on.install, self._on_install)
        self.framework.observe(self.charm.on.start, self._on_start)
        self.framework.observe(self.charm.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_created, self._on_peer_relation_created
        )
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_joined, self._on_peer_relation_joined
        )
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_changed, self._on_peer_relation_changed
        )
        self.framework.observe(
            self.charm.on[PEER_RELATION].relation_departed, self._on_peer_relation_departed
        )
        self.framework.observe(self.charm.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.charm.on.update_status, self._on_update_status)
        self.framework.observe(self.charm.on.secret_changed, self._on_secret_changed)
        self.framework.observe(
            self.charm.on[DATA_STORAGE].storage_detaching, self._on_storage_detaching
        )
        self.framework.observe(
            self.charm.on[DATA_STORAGE].storage_attached, self._on_storage_attached
        )
        self.framework.observe(
            self.charm.on.rebuild_cluster_action, self._on_rebuild_cluster_action
        )

    def _on_storage_attached(self, event: ops.StorageAttachedEvent) -> None:
        """Handle storage attachment."""
        # fix the permissions of the data dir if re-attaching existing storage
        for path in [SNAP_DATA_PATH, SNAP_LOG_PATH, SNAP_ARCHIVE_PATH]:
            self.charm.workload.exec(["chmod", "-R", "750", path])
            self.charm.workload.exec(["chown", "-R", f"{SNAP_USER}:{SNAP_GROUP}", path])

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle install event."""
        try:
            self.charm.workload.install()
        except EtcdServiceError:
            self.charm.status.set_running_status(
                EtcdServiceStatuses.SERVICE_NOT_INSTALLED.value,
                scope="unit",
                component_name=self.charm.cluster_manager.name,
                statuses_state=self.charm.state.statuses,
            )
            raise EtcdServiceError(
                "Failed to install the etcd snap. Check the logs for more details."
            )

    def _on_start(self, event: ops.StartEvent) -> None:  # noqa: C901
        """Handle start event."""
        if self.charm.state.cluster.rebuild_cluster_in_progress:
            logger.info("Do not start, orchestration is handled by rebuild-cluster workflow.")
            return

        # check if data exists before doing any operation
        storage_reuse = self.charm.workload.exists(DATABASE_DIR)

        tls_transition_states = [TLSState.TO_TLS, TLSState.TO_NO_TLS]
        if (
            self.charm.state.unit_server.tls_client_state in tls_transition_states
            or self.charm.state.unit_server.tls_peer_state in tls_transition_states
        ):
            logger.info(
                f"Deferring start because TLS is not ready for {self.charm.state.unit_server.member_name}."
            )
            event.defer()
            return

        self.charm.config_manager.set_config_properties()

        if not self.charm.state.cluster.model.cluster_state and self.charm.unit.is_leader():
            # this is the very first cluster start, this unit starts without being added as member
            # all subsequent units will have to be added as member before starting the workload
            self.charm.status.set_running_status(
                EtcdServiceStatuses.SERVICE_STARTING.value,
                scope="unit",
                component_name=self.charm.cluster_manager.name,
                statuses_state=self.charm.state.statuses,
            )
            self.charm.cluster_manager.start_member()
            # overwrite the `force-new-cluster` config after cluster has been initialized
            self.charm.config_manager.set_config_properties()

            if storage_reuse:
                # this is a new application but storage is reused
                # update cluster membership configuration after recovering existing data
                try:
                    self.charm.cluster_manager.broadcast_peer_url(
                        self.charm.state.unit_server.peer_url
                    )
                    self.charm.state.cluster.update({"authentication": "enabled"})
                except ValueError:
                    logger.error("Failed to update member configuration")
                    event.defer()
                    return

            if not self.charm.state.cluster.auth_enabled:
                try:
                    self.charm.cluster_manager.enable_authentication()
                    self.charm.state.cluster.update({"authentication": "enabled"})
                except (EtcdAuthNotEnabledError, EtcdUserManagementError) as e:
                    logger.error(e)
                    raise
        elif (
            self.charm.state.unit_server.member_endpoint
            in self.charm.state.cluster.model.cluster_members
        ):
            # this unit has been added to the etcd cluster
            if not self.charm.state.cluster.auth_enabled:
                # failed start hooks on cluster initialization will go here on retry
                if self.charm.unit.is_leader():
                    try:
                        self.charm.cluster_manager.enable_authentication()
                        self.charm.state.cluster.update({"authentication": "enabled"})
                    except (EtcdAuthNotEnabledError, EtcdUserManagementError) as e:
                        logger.error(e)
                        event.defer()
                        return
                else:
                    logger.error("Authentication not enabled.")
                    event.defer()
                    return

            if not self.charm.state.unit_server.is_started:
                # database files should not be deleted on running units
                if storage_reuse:
                    logger.warning(f"Existing database file detected in {DATABASE_DIR}.")
                    # storage cannot be reused on non-leader members
                    try:
                        self.charm.workload.remove_directory(DATABASE_DIR)
                        logger.warning(
                            f"Removed database file from {DATABASE_DIR} to join existing cluster."
                        )
                    except OSError:
                        # if removing fails, we cannot start the workload or the member would crash
                        raise

            # if the flag `unit_server.is_started` is missing, this was an uncontrolled reboot
            self.charm.status.set_running_status(
                EtcdServiceStatuses.SERVICE_STARTING.value,
                scope="unit",
                component_name=self.charm.cluster_manager.name,
                statuses_state=self.charm.state.statuses,
            )
            self.charm.cluster_manager.start_member()
        else:
            # this unit that has not yet been added to the cluster
            # wait for leader to process `relation_joined` event and add the member to the cluster
            event.defer()
            return

        if self.charm.workload.alive():
            logger.info("Workload started successfully. Opening client port")
            self.charm.unit.open_port("tcp", CLIENT_PORT)
            self.charm.state.statuses.delete(
                EtcdServiceStatuses.SERVICE_STARTING.value,
                scope="unit",
                component=self.charm.cluster_manager.name,
            )
        else:
            logger.error("Workload failed to start.")
            self.charm.status.set_running_status(
                EtcdServiceStatuses.SERVICE_NOT_RUNNING.value,
                scope="unit",
                component_name=self.charm.cluster_manager.name,
                statuses_state=self.charm.state.statuses,
            )

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config_changed event."""
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
        ):
            logger.warning(
                "Cannot update config while cluster is in vulnerable state because of restore or cluster-rebuild"
            )
            event.defer()
            return

        # refresh the host information and cluster membership in case of ip change
        ip_address = self.charm.workload.get_host_mapping().get("private_ip")
        if (
            ip_address
            and self.charm.state.unit_server.model
            and ip_address != self.charm.state.unit_server.model.private_ip
        ):
            logger.info(f"New ip address: {ip_address}")
            self.charm.state.unit_server.update(self.charm.workload.get_host_mapping())

            # we need to update the client-urls by restarting etcd
            self.charm.config_manager.set_config_properties()
            # after ip change, this member is unavailable, no need to acquire restart lock
            if not self.charm.cluster_manager.restart_member(move_leader=False):
                raise HealthCheckFailedError("Failed to check health of the member after restart")

            # update cluster configuration
            self.charm.cluster_manager.broadcast_peer_url(self.charm.state.unit_server.peer_url)
            if self.charm.unit.is_leader():
                self.charm.cluster_manager.update_cluster_member_state()

            # update tls certificates with new ip address
            if self.charm.state.unit_server.tls_client_state in (
                TLSState.TO_TLS,
                TLSState.TLS,
            ) or self.charm.state.unit_server.tls_peer_state in (TLSState.TO_TLS, TLSState.TLS):
                self.charm.tls_events.refresh_tls_certificates_event.emit()

        # we can only handle this now as we must update ip addresses during long-running upgrades
        if self.charm.refresh_in_progress:
            logger.warning(
                "Cannot update config while cluster is in vulnerable state because of refresh"
            )
            event.defer()
            return

        if (
            self.charm.config_manager.are_tuning_parameters_valid()
            and self.charm.config_manager.requires_restart()
        ):
            # apply config and initiate restart
            self.charm.rolling_restart()

        if not self.charm.unit.is_leader():
            return

        if admin_secret_id := self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
            self.update_admin_password(admin_secret_id)

    def _on_peer_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle event received by a new unit when joining the cluster relation."""
        self.charm.state.unit_server.update(self.charm.workload.get_host_mapping())

    def _on_peer_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle all events related to the cluster-peer relation."""
        if self.charm.state.cluster.is_restore_in_progress:
            return

        if self.charm.state.cluster.rebuild_cluster_in_progress:
            try:
                self._rebuild_cluster()
            except EtcdClusterManagementError as e:
                # if adding a member or promoting a learner fails, we want to re-run this again
                logger.warning(e)
                event.defer()

            return

        if self.charm.unit.is_leader():
            if self.charm.state.cluster.model.learning_member:
                try:
                    # this will promote any learner, not only the unit that updated its relation data
                    self.charm.cluster_manager.promote_learning_member()
                except EtcdClusterManagementError as e:
                    logger.warning(e)
                    event.defer()
                    return

            # reflect membership updates in the cluster state and config file in case of restarts
            # e.g. ip change or tls switchover
            self.charm.cluster_manager.update_cluster_member_state()

            self._update_client_relations()

        for tls_type in TLSType:
            try:
                self.charm.tls_manager.check_certificate_validity(tls_type)
                self.charm.state.unit_server.update(
                    {f"tls_{tls_type.value}_certificates_expiring": ""}
                )
            except CalledProcessError:
                self.charm.state.unit_server.update(
                    {f"tls_{tls_type.value}_certificates_expiring": "True"}
                )

        # update client CA truststore if changed
        all_cas = self.charm.tls_manager.collect_client_cas()
        if all_cas != self.charm.tls_manager.load_trusted_ca(TLSType.CLIENT):
            logger.debug("CAs have changed, updating client truststore")
            self.charm.tls_manager.update_cas(all_cas, TLSType.CLIENT)
            self.charm.rolling_restart()

    def _on_peer_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle event received by all units when a unit leaves the cluster relation."""
        if not self.charm.unit.is_leader():
            return

        if self.charm.state.unit_server.is_started:
            # this must not overwrite already cleaned up application databag
            # it should only happen if at least this unit's workload is still running
            logger.debug(f"Removing {event.unit.name} from cluster state in peer relation.")
            cluster_members = self.charm.state.cluster.model.cluster_members.split(",")
            # re-assemble the string without the departing unit
            updated_cluster_members = ",".join(
                m for m in cluster_members if event.unit.name.replace("/", "") not in m
            )
            self.charm.state.cluster.update({"cluster_members": updated_cluster_members})

    def _on_peer_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handle event received by all units when a new unit joins the cluster relation."""
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
        ):
            logger.warning(
                "Cannot add cluster member while a restore or cluster-rebuild operation is in progress."
            )
            return

        if self.charm.unit.is_leader():
            try:
                self.charm.cluster_manager.add_member(event.unit.name)
            except (EtcdClusterManagementError, KeyError) as e:
                logger.warning(e)
                event.defer()
                return

    def _on_leader_elected(self, event: LeaderElectedEvent) -> None:
        """Handle all events in the 'cluster' peer relation."""
        if not self.charm.state.peer_relation:
            event.defer()
            return
        if self.charm.unit.is_leader() and not self.charm.state.cluster.internal_user_credentials:
            if admin_secret_id := self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
                try:
                    password = self.charm.state.get_secret_from_id(str(admin_secret_id)).get(
                        INTERNAL_USER
                    )
                except (ModelError, SecretNotFoundError) as e:
                    logger.error(f"Could not access secret {admin_secret_id}: {e}")
                    raise
            else:
                password = self.charm.workload.generate_password()

            self.charm.state.cluster.update({"internal_user_credentials": password})

        try:
            if self.charm.cluster_manager.is_cluster_failed:
                return
        except RequestException:
            # if anything fails with the metrics request, we don't want to panic
            pass

        # reflect membership updates in the cluster state
        self.charm.cluster_manager.update_cluster_member_state()

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Handle update_status event."""
        if (
            not self.charm.state.cluster.model.cluster_state
            or self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
            or self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
            or self.charm.state.unit_server.tls_peer_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            return

        if not self.charm.workload.alive() and not self.charm.refresh_in_progress:
            if not self.charm.cluster_manager.restart_member():
                self.charm.status.set_running_status(
                    EtcdServiceStatuses.SERVICE_NOT_RUNNING.value,
                    scope="unit",
                    component_name=self.charm.cluster_manager.name,
                    statuses_state=self.charm.state.statuses,
                )
                return

        try:
            if self.charm.cluster_manager.is_cluster_failed:
                return
        except RequestException:
            # if anything fails with the metrics request, we don't want to panic
            pass

        if self.charm.unit.is_leader() and not self.charm.refresh_in_progress:
            try:
                self.charm.cluster_manager.clean_users()
                self.charm.cluster_manager.remove_inconsistent_members_if_required()
                self.charm.state.statuses.delete(
                    ClusterStatuses.CLUSTER_MANAGEMENT_ERROR.value,
                    scope="unit",
                    component=self.charm.cluster_manager.name,
                )
            except (
                AttributeError,
                KeyError,
                ValueError,
                EtcdClusterManagementError,
                EtcdUserManagementError,
            ) as e:
                logger.error(e)
                self.charm.status.set_running_status(
                    ClusterStatuses.CLUSTER_MANAGEMENT_ERROR.value,
                    scope="unit",
                    component_name=self.charm.cluster_manager.name,
                    statuses_state=self.charm.state.statuses,
                )

        for tls_type in TLSType:
            try:
                self.charm.tls_manager.check_certificate_validity(tls_type)
                self.charm.state.unit_server.update(
                    {f"tls_{tls_type.value}_certificates_expiring": ""}
                )
            except CalledProcessError:
                self.charm.state.unit_server.update(
                    {f"tls_{tls_type.value}_certificates_expiring": "True"}
                )

    def _on_secret_changed(self, event: ops.SecretChangedEvent) -> None:
        """Handle the secret_changed event."""
        if not self.charm.unit.is_leader():
            return

        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
            or self.charm.refresh_in_progress
        ):
            logger.warning(
                "Cannot update credentials while cluster is in vulnerable state because of restore, refresh or cluster-rebuild"
            )
            event.defer()
            return

        if admin_secret_id := self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
            if admin_secret_id == event.secret.id:
                self.update_admin_password(admin_secret_id)

    def _on_rebuild_cluster_action(self, event: ActionEvent) -> None:
        """Recover from majority failure by rebuilding the cluster membership configuration."""
        if error := self._check_rebuild_preventing_reason():
            event.set_results({"error": error})
            event.fail(error)
            return

        try:
            # safeguard to avoid users wrecking fine clusters
            if not self.charm.cluster_manager.is_cluster_failed and not event.params.get(
                "force", False
            ):
                event.fail("Cluster has not failed. Use `force` to rebuild anyway.")
                return
        except RequestException:
            logger.warning("Could not determine if cluster failed - continue with cluster rebuild")

        logger.info("Cluster rebuild initiated.")
        logger.info("Stopping and disabling etcd workload.")
        # disable the service to avoid restart while workflow is in progress
        self.charm.workload.disable_database()
        self.charm.state.unit_server.update({"state": ""})
        self.charm.state.unit_server.update({"rebuild_completed": ""})
        self.charm.state.cluster.update({"rebuild_cluster": "True"})
        self.charm.state.cluster.update({"cluster_members": ""})
        event.set_results({"result": "cluster rebuild in progress"})

    def _on_storage_detaching(self, event: ops.StorageDetachingEvent) -> None:
        """Handle removal of the data storage mount, e.g. when removing a unit."""
        if self.charm.app.planned_units() > 0:
            if not (
                self.charm.state.cluster.is_restore_in_progress
                or self.charm.state.cluster.rebuild_cluster_in_progress
            ):
                # allow for unit removal when restore is in progress
                try:
                    self.charm.cluster_manager.remove_member()
                except (EtcdClusterManagementError, RaftLeaderNotFoundError):
                    # We want this hook to error out if we cannot remove the cluster member
                    # otherwise the cluster could become unavailable because of quorum loss
                    raise
        else:
            logger.info("Removing last unit from etcd cluster.")
            if self.charm.unit.is_leader():
                self.charm.state.cluster.update(
                    {
                        "cluster_state": "",
                        "cluster_members": "",
                        "authentication": "",
                    }
                )

        self.charm.workload.stop()
        self.charm.state.unit_server.update({"state": ""})
        self.charm.status.set_running_status(
            ClusterStatuses.REMOVED.value,
            scope="unit",
            component_name=self.charm.cluster_manager.name,
            statuses_state=self.charm.state.statuses,
        )

    def update_admin_password(self, admin_secret_id: str) -> None:
        """Compare current admin password and update in etcd if required."""
        errored = False
        try:
            if new_password := self.charm.state.get_secret_from_id(admin_secret_id).get(
                INTERNAL_USER
            ):
                # only update admin credentials if the password has changed
                if new_password != self.charm.state.cluster.internal_user_credentials.get(
                    INTERNAL_USER
                ):
                    logger.debug(f"{INTERNAL_USER_PASSWORD_CONFIG} have changed.")
                    try:
                        self.charm.cluster_manager.update_credentials(
                            username=INTERNAL_USER, password=new_password
                        )
                        self.charm.state.cluster.update(
                            {"internal_user_credentials": new_password}
                        )
                    except EtcdUserManagementError as e:
                        logger.error(e)
                        self.charm.status.set_running_status(
                            ClusterStatuses.PASSWORD_UPDATE_FAILED.value,
                            scope="app",
                            component_name=self.charm.cluster_manager.name,
                            statuses_state=self.charm.state.statuses,
                        )
                        errored = True
            else:
                logger.error(f"Invalid username in secret {admin_secret_id}.")
                self.charm.status.set_running_status(
                    ClusterStatuses.PASSWORD_UPDATE_FAILED.value,
                    scope="app",
                    component_name=self.charm.cluster_manager.name,
                    statuses_state=self.charm.state.statuses,
                )
                errored = True
        except (ModelError, SecretNotFoundError) as e:
            logger.error(e)
            self.charm.status.set_running_status(
                ClusterStatuses.PASSWORD_UPDATE_FAILED.value,
                scope="app",
                component_name=self.charm.cluster_manager.name,
                statuses_state=self.charm.state.statuses,
            )
            errored = True

        if not errored:
            self.charm.state.statuses.delete(
                ClusterStatuses.PASSWORD_UPDATE_FAILED.value,
                scope="app",
                component=self.charm.cluster_manager.name,
            )

    def _rebuild_cluster(self) -> None:  # noqa: C901
        """Rebuild cluster with new membership configuration, to recover from majority failure.

        This method handles all the logic for the rebuild-cluster workflow, initiated by running
        the action `rebuild-cluster` on the Juju leader.

        The workflow consists of the following steps:
        - stop etcd on all units
        - initialise the cluster with new membership configuration
        - start etcd on the Juju leader
        - add other units one-by-one as new member and start it
        - perform a cluster health check on the Juju leader after all units are added and started

        If the health check fails or any of the units error during the process, the cluster stays
        stuck and the flag `rebuild_cluster` in the app-databag doesn't get reset. This is shown
        with a `BlockedStatus`. Users should then investigate (e.g. force-remove a faulty unit)
        and re-run the action.
        """
        if self.charm.unit.is_leader():
            if not any(unit.is_started for unit in self.charm.state.servers):
                logger.info("All units stopped - initialise new cluster configuration.")
                self.charm.state.cluster.update({"cluster_state": ""})
                self.charm.config_manager.set_config_properties()

                logger.info("Enabling and starting etcd again.")
                self.charm.workload.enable_service()
                self.charm.cluster_manager.start_member()
                # overwrite the `force-new-cluster` config after cluster has been rebuilt
                self.charm.config_manager.set_config_properties()
                self.charm.state.unit_server.update({"rebuild_completed": "True"})
            elif (
                all(unit.rebuild_completed for unit in self.charm.state.servers)
                and not self.charm.state.cluster.model.learning_member
                and self.charm.cluster_manager.is_healthy()
            ):
                logger.info("All units started again - cluster rebuild completed.")
                self.charm.state.cluster.update({"rebuild_cluster": ""})

            if self.charm.state.cluster.model.learning_member:
                self.charm.cluster_manager.promote_learning_member()

            if self.charm.state.unit_server.rebuild_completed:
                # after leader has started again, subsequently add all other units
                for unit in self.charm.state.servers:
                    if unit.member_endpoint not in self.charm.state.cluster.model.cluster_members:
                        # we only add one learner at a time to not overload the raft leader
                        self.charm.cluster_manager.add_member(unit.unit_name)
                        break

            return

        # this is the workflow for non-leader units
        if not self.charm.state.cluster.model.cluster_members:
            # the action was executed on the leader, cluster member configuration was cleared
            # clean up in case a previous run failed
            self.charm.state.unit_server.update({"rebuild_completed": ""})

        if (
            self.charm.state.unit_server.is_started
            and not self.charm.state.unit_server.rebuild_completed
        ):
            # shutdown phase
            logger.info("Stopping and disabling etcd workload.")
            self.charm.workload.disable_database()
            logger.warning(f"Removing database file from {DATABASE_DIR} for cluster rebuild.")
            try:
                self.charm.workload.remove_directory(DATABASE_DIR)
            except FileNotFoundError:
                logger.info(f"No database file found in {DATABASE_DIR} - nothing to remove")
            self.charm.state.unit_server.update({"state": ""})
            return

        if (
            self.charm.state.unit_server.member_endpoint
            in self.charm.state.cluster.model.cluster_members
            and not self.charm.state.unit_server.is_started
        ):
            # startup phase
            logger.info("Enabling and starting etcd again.")
            self.charm.config_manager.set_config_properties()
            self.charm.workload.enable_service()
            self.charm.cluster_manager.start_member()
            self.charm.state.unit_server.update({"rebuild_completed": "True"})

    def _check_rebuild_preventing_reason(self) -> str:
        """Check if an action can be executed, if not return error message.

        Returns:
            Error message in case a preventing reason for an action exists, otherwise empty str.
        """
        if not self.charm.unit.is_leader():
            return "Action must be performed on the leader unit."

        if self.charm.state.cluster.is_backup_in_progress:
            return "Backup in progress, cannot perform action."

        if self.charm.state.cluster.is_restore_in_progress:
            return "Restore in progress, cannot perform action."

        return ""

    def _update_client_relations(self) -> None:
        if self.charm.state.unit_server.tls_client_state == TLSState.TLS:
            try:
                self.charm.external_clients_manager.update_client_relations_data(
                    etcd_version=self.charm.cluster_manager.get_version()
                )
            except KeyError as e:
                logger.warning(f"Error updating client relations data: {e}")
