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

from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdClusterManagementError,
    EtcdUserManagementError,
    HealthCheckFailedError,
    RaftLeaderNotFoundError,
)
from common.secrets import get_secret_from_id
from literals import (
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
    TLS_CLIENT_PRIVATE_KEY_CONFIG,
    TLS_PEER_PRIVATE_KEY_CONFIG,
    Status,
    TLSState,
    TLSType,
)

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
        if not self.charm.workload.install():
            self.charm.set_status(Status.SERVICE_NOT_INSTALLED)
            return

    def _on_start(self, event: ops.StartEvent) -> None:  # noqa: C901
        """Handle start event."""
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

        if not self.charm.state.cluster.cluster_state and self.charm.unit.is_leader():
            # this is the very first cluster start, this unit starts without being added as member
            # all subsequent units will have to be added as member before starting the workload
            self.charm.cluster_manager.start_member()

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
            in self.charm.state.cluster.cluster_members
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

                self.charm.set_status(Status.SERVICE_STARTING)
                self.charm.cluster_manager.start_member()
        else:
            # this unit that has not yet been added to the cluster
            # wait for leader to process `relation_joined` event and add the member to the cluster
            self.charm.set_status(Status.CLUSTER_NOT_JOINED)
            event.defer()
            return

        if not self.charm.workload.alive():
            self.charm.set_status(Status.SERVICE_NOT_RUNNING)

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        """Handle config_changed event."""
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
        ):
            logger.warning("Cannot update config while another operation is in progress.")
            event.defer()
            return

        # refresh the host information and cluster membership in case of ip change
        ip_address = self.charm.cluster_manager.get_host_mapping().get("ip")
        if ip_address != self.charm.state.unit_server.ip:
            logger.info(f"New ip address: {ip_address}")
            self.charm.state.unit_server.update({"ip": ip_address})

            # update cluster configuration
            self.charm.cluster_manager.broadcast_peer_url(self.charm.state.unit_server.peer_url)
            self.charm.config_manager.set_config_properties()

            # we need to update the client-urls by restarting etcd
            # after ip change, this member is unavailable, no need to acquire restart lock
            if not self.charm.cluster_manager.restart_member(move_leader=False):
                raise HealthCheckFailedError("Failed to check health of the member after restart")

            if self.charm.unit.is_leader():
                self.charm.cluster_manager.update_cluster_member_state()

            # update tls certificates with new ip address
            if self.charm.state.unit_server.tls_client_state in (
                TLSState.TO_TLS,
                TLSState.TLS,
            ) or self.charm.state.unit_server.tls_peer_state in (TLSState.TO_TLS, TLSState.TLS):
                self.charm.tls_events.refresh_tls_certificates_event.emit()

        if tls_peer_private_key_id := self.charm.config.get(TLS_PEER_PRIVATE_KEY_CONFIG):
            self.update_private_key(tls_peer_private_key_id)

        if tls_client_private_key_id := self.charm.config.get(TLS_CLIENT_PRIVATE_KEY_CONFIG):
            self.update_private_key(tls_client_private_key_id)

        if not self.charm.unit.is_leader():
            return

        if admin_secret_id := self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
            self.update_admin_password(admin_secret_id)

    def _on_peer_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle event received by a new unit when joining the cluster relation."""
        self.charm.state.unit_server.update(self.charm.cluster_manager.get_host_mapping())

    def _on_peer_relation_changed(self, event: RelationChangedEvent) -> None:
        """Handle all events related to the cluster-peer relation."""
        if self.charm.state.cluster.is_restore_in_progress:
            return

        if self.charm.state.cluster.rebuild_cluster_in_progress:
            try:
                self._rebuild_cluster()
                return
            except EtcdClusterManagementError as e:
                # if adding a member or promoting a learner fails, we want to re-run this again
                logger.warning(e)
                event.defer()
                return

        if self.charm.unit.is_leader():
            if self.charm.state.cluster.learning_member:
                try:
                    # this will promote any learner, not only the unit that updated its relation data
                    self.charm.cluster_manager.promote_learning_member()
                except EtcdClusterManagementError as e:
                    logger.warning(e)
                    self.charm.set_status(Status.CLUSTER_MEMBER_NOT_PROMOTED)
                    event.defer()
                    return

            # reflect membership updates in the cluster state, e.g. ip change or tls switchover
            self.charm.cluster_manager.update_cluster_member_state()

            try:
                self.charm.external_clients_manager.update_client_relations_data(
                    etcd_version=self.charm.cluster_manager.get_version()
                )
            except KeyError as e:
                logger.warning(f"Error updating client relations data: {e}")

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

    def _on_peer_relation_departed(self, event: RelationDepartedEvent) -> None:
        """Handle event received by all units when a unit leaves the cluster relation."""
        if not self.charm.unit.is_leader():
            return

        if self.charm.state.unit_server.is_started:
            # this must not overwrite already cleaned up application databag
            # it should only happen if at least this unit's workload is still running
            logger.debug(f"Removing {event.unit.name} from cluster state in peer relation.")
            cluster_members = self.charm.state.cluster.cluster_members.split(",")
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
            logger.warning("Cannot add cluster member while another operation is in progress.")
            event.defer()
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
            self.charm.set_status(Status.NO_PEER_RELATION)
            return

        if self.charm.unit.is_leader() and not self.charm.state.cluster.internal_user_credentials:
            if admin_secret_id := self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
                try:
                    password = get_secret_from_id(self.charm.model, admin_secret_id).get(
                        INTERNAL_USER
                    )
                except (ModelError, SecretNotFoundError) as e:
                    logger.error(f"Could not access secret {admin_secret_id}: {e}")
                    raise
            else:
                password = self.charm.workload.generate_password()

            self.charm.state.cluster.update({f"{INTERNAL_USER}-password": password})

        try:
            if self.charm.cluster_manager.is_cluster_failed:
                self.charm.set_status(Status.CLUSTER_FAILED)
                return
        except RuntimeError:
            # if anything fails with the metrics request, we don't want to panic
            pass

        # reflect membership updates in the cluster state
        self.charm.cluster_manager.update_cluster_member_state()

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Handle update_status event."""
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
        ):
            return

        if not self.charm.workload.alive():
            if not self.charm.cluster_manager.restart_member():
                self.charm.set_status(Status.SERVICE_NOT_RUNNING)
                return

        try:
            if self.charm.cluster_manager.is_cluster_failed:
                self.charm.set_status(Status.CLUSTER_FAILED)
                return
        except RuntimeError:
            # if anything fails with the metrics request, we don't want to panic
            pass

        if self.charm.unit.is_leader():
            try:
                self.charm.cluster_manager.clean_users()
                self.charm.cluster_manager.remove_inconsistent_members_if_required()
            except (
                AttributeError,
                ValueError,
                EtcdClusterManagementError,
                EtcdUserManagementError,
            ) as e:
                logger.error(e)
                self.charm.set_status(Status.CLUSTER_MANAGEMENT_ERROR)

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
        if tls_peer_private_key_id := self.charm.config.get(TLS_PEER_PRIVATE_KEY_CONFIG):
            if tls_peer_private_key_id == event.secret.id:
                self.update_private_key(tls_peer_private_key_id)

        if tls_client_private_key_id := self.charm.config.get(TLS_CLIENT_PRIVATE_KEY_CONFIG):
            if tls_client_private_key_id == event.secret.id:
                self.update_private_key(tls_client_private_key_id)

        if not self.charm.unit.is_leader():
            return

        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
        ):
            logger.warning("Cannot update credentials while another operation is in progress.")
            event.defer()
            return

        if admin_secret_id := self.charm.config.get(INTERNAL_USER_PASSWORD_CONFIG):
            if admin_secret_id == event.secret.id:
                self.update_admin_password(admin_secret_id)

    def _on_rebuild_cluster_action(self, event: ActionEvent) -> None:
        """Recover from majority failure by rebuilding the cluster membership configuration."""
        if error := self._exists_preventing_reason():
            event.set_results({"error": error})
            event.fail(error)
            return

        logger.info("Cluster rebuild initiated.")
        logger.info("Stopping and disabling etcd workload.")
        # disable the service to avoid restart while workflow is in progress
        self.charm.workload.disable_database()
        self.charm.state.unit_server.update({"state": ""})
        self.charm.state.cluster.update({"rebuild_cluster": "True"})
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
        self.charm.set_status(Status.REMOVED)

    def update_admin_password(self, admin_secret_id: str) -> None:
        """Compare current admin password and update in etcd if required."""
        try:
            if new_password := get_secret_from_id(self.charm.model, admin_secret_id).get(
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
                            {f"{INTERNAL_USER}-password": new_password}
                        )
                    except EtcdUserManagementError as e:
                        logger.error(e)
                        self.charm.set_status(Status.PASSWORD_UPDATE_FAILED)
            else:
                logger.error(f"Invalid username in secret {admin_secret_id}.")
                self.charm.set_status(Status.PASSWORD_UPDATE_FAILED)
        except (ModelError, SecretNotFoundError) as e:
            logger.error(e)
            self.charm.set_status(Status.PASSWORD_UPDATE_FAILED)

    def update_private_key(self, private_key_id: str) -> None:
        """Update the private key in etcd."""
        logger.debug("Updating TLS private key.")

        if self.charm.tls_events.read_and_validate_private_key(private_key_id) is None:
            self.charm.set_status(Status.TLS_INVALID_PRIVATE_KEY)
            return

        self.charm.tls_events.refresh_tls_certificates_event.emit()

    def _rebuild_cluster(self) -> None:
        """Rebuild cluster with new membership configuration, to recover from majority failure.

        This method handles all the logic for the rebuild-cluster workflow, initiated by running
        the action `rebuild-cluster` on the Juju leader.

        The workflow consists of the following steps:
        - stop etcd on all units
        - initialise the cluster with new membership configuration
        - start etcd on all units
        - perform a cluster health check on the Juju leader

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
                self.charm.state.unit_server.update({"rebuild_completed": "True"})
            elif (
                all(unit.rebuild_completed for unit in self.charm.state.servers)
                and self.charm.cluster_manager.is_healthy()
            ):
                logger.info("All units started again - cluster rebuild completed.")
                self.charm.state.cluster.update({"rebuild_cluster": ""})

            if self.charm.state.cluster.learning_member:
                self.charm.cluster_manager.promote_learning_member()

            for unit in self.charm.state.servers:
                if unit.member_endpoint not in self.charm.state.cluster.cluster_members:
                    # we only add one learner at a time to not overload the raft leader
                    self.charm.cluster_manager.add_member(unit.unit_name)
                    break

            return

        # this is the workflow for non-leader units
        if (
            self.charm.state.unit_server.is_started
            and not self.charm.state.unit_server.rebuild_completed
        ):
            logger.info("Stopping and disabling etcd workload.")
            self.charm.workload.disable_database()
            self.charm.state.unit_server.update({"state": ""})
        elif (
            self.charm.state.unit_server.member_endpoint
            in self.charm.state.cluster.cluster_members
        ):
            logger.info("Enabling and starting etcd again.")
            self.charm.config_manager.set_config_properties()
            self.charm.workload.enable_service()
            self.charm.cluster_manager.start_member()
            self.charm.state.unit_server.update({"rebuild_completed": "True"})

    def _exists_preventing_reason(self) -> str:
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
