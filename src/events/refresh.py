#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handlers for in-place upgrades."""

import dataclasses
import logging
from typing import TYPE_CHECKING

import charm_refresh

from common.exceptions import EtcdUpgradeError
from literals import TLSCARotationState, TLSState
from statuses import ClusterStatuses

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


@dataclasses.dataclass(eq=False)
class MachinesEtcdRefresh(charm_refresh.CharmSpecificMachines):
    """Refresh handler for Etcd operator on machines substrate."""

    charm: "EtcdOperatorCharm"

    @classmethod
    def is_compatible(
        cls,
        *,
        old_charm_version: charm_refresh.CharmVersion,
        new_charm_version: charm_refresh.CharmVersion,
        old_workload_version: str,
        new_workload_version: str,
    ) -> bool:
        """Check charm version compatibility."""
        if not super().is_compatible(
            old_charm_version=old_charm_version,
            new_charm_version=new_charm_version,
            old_workload_version=old_workload_version,
            new_workload_version=new_workload_version,
        ):
            return False

        # Check workload version compatibility
        return cls.is_workload_compatible(
            old_workload_version=old_workload_version,
            new_workload_version=new_workload_version,
        )

    def refresh_snap(
        self,
        *,
        snap_name: str,
        snap_revision: str,
        refresh: charm_refresh.Machines,
    ) -> None:
        """Refresh the snap for the etcd charm."""
        self.charm.cluster_manager.move_leader_if_required()
        self.charm.workload.stop()

        revision_before_refresh = self.charm.workload.snap_revision()
        assert snap_revision != revision_before_refresh, (
            "current snap revision and target revision are equal"
        )

        logger.info("Updating snap installation")
        if not self.charm.workload.install(revision=snap_revision, retry_and_raise=False):
            logger.exception("Snap refresh failed")

            if self.charm.workload.snap_revision() == revision_before_refresh:
                self.charm.workload.start()
            else:
                refresh.update_snap_revision()

            # must raise an uncaught exception her to ensure the unit receives another Juju event
            raise EtcdUpgradeError("Snap refresh failed")

        refresh.update_snap_revision()
        logger.info(f"Updated snap to revision {snap_revision}")

        logger.info("Restarting workload")
        # always apply the current charm revision's config -> no need to "migrate" configuration
        # this charm revision's config is the one supported by the targeted workload version
        self.charm.config_manager.set_config_properties()
        self.charm.workload.start()
        if self.charm.cluster_manager.is_healthy():
            refresh.next_unit_allowed_to_refresh = True
        else:
            self.charm.status.set_running_status(
                ClusterStatuses.HEALTH_CHECK_FAILED.value,
                scope="unit",
                component_name="upgrades",
                statuses_state=self.charm.state.statuses,
            )

    def run_pre_refresh_checks_after_1_unit_refreshed(self) -> None:
        """Implement pre-refresh checks."""
        if self.charm.state.cluster.is_backup_in_progress:
            raise charm_refresh.PrecheckFailed("Backup in progress")

        if self.charm.state.cluster.is_restore_in_progress:
            raise charm_refresh.PrecheckFailed("Restore in progress")

        if self.charm.state.cluster.rebuild_cluster_in_progress:
            raise charm_refresh.PrecheckFailed("Cluster rebuild in progress")

        if (
            self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
            or self.charm.state.unit_server.tls_peer_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            raise charm_refresh.PrecheckFailed("TLS CA rotation is in progress")

        tls_transition_states = [TLSState.TO_TLS, TLSState.TO_NO_TLS]
        if (
            self.charm.state.unit_server.tls_client_state in tls_transition_states
            or self.charm.state.unit_server.tls_peer_state in tls_transition_states
        ):
            raise charm_refresh.PrecheckFailed("TLS transition is in progress")

        if not self.charm.cluster_manager.is_healthy():
            raise charm_refresh.PrecheckFailed("Cluster is not healthy")

    @staticmethod
    def is_workload_compatible(
        old_workload_version: str,
        new_workload_version: str,
    ) -> bool:
        """Check if the workload versions are compatible.

        This method is called on the new charm code version. This means that it is responsible for
        determining which versions the charm code supports refreshing from - not refreshing to.
        """
        try:
            old_major, old_minor, old_patch, *_ = (
                int(component) for component in old_workload_version.split(".")
            )
            new_major, new_minor, new_patch, *_ = (
                int(component) for component in new_workload_version.split(".")
            )
        except ValueError:
            # Not enough values to unpack or cannot convert
            logger.error(
                "Unable to parse workload versions."
                f"Got {old_workload_version} to {new_workload_version}"
            )
            return False

        if old_major != new_major:
            logger.info(
                "Refreshing to a different major version workload is not supported. "
                f"Got {old_major} to {new_major}"
            )
            return False

        if new_minor != old_minor:
            # Minor version upgrades are treated like major version upgrades in etcd (separate track).
            # Technically it is possible to allow minor version upgrades from a different track.
            # Restriction: base operating system must be kept the same
            # Recommendation from charm-refresh: only allow upgrades from a different track for a
            # specific set of workload versions
            # Once we move to a new track, this part should be adjusted to allow upgrade to this track.
            # The condition should then be:
            # if not (new_minor == old_minor)
            # or (new_minor == old_minor + 1
            #   and old_patch == specific_old_patch_version
            #   and new_patch == specific_new_patch_version
            # )
            # return False
            logger.info(
                "Refreshing to a different minor version workload is not supported. "
                f"Got {old_major}.{old_minor}.{old_patch} to {new_major}.{new_minor}.{new_patch}"
            )
            return False

        if new_patch < old_patch:
            # Once we move to a new track, adjust here to allow upgrades to lower patch version.
            logger.info(
                "Downgrading to a previous patch version workload is not supported. "
                f"Got {old_major}.{old_minor}.{old_patch} to {new_major}.{new_minor}.{new_patch}"
            )
            return False

        return True
