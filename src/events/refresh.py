#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Handlers for in-place upgrades."""

import dataclasses
import logging
from typing import TYPE_CHECKING

import charm_refresh

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
        return is_workload_compatible(
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
        pass

    def run_pre_refresh_checks_after_1_unit_refreshed(self) -> None:
        """Implement pre-refresh checks after 1 unit refreshed."""
        pass


def is_workload_compatible(
    old_workload_version: str,
    new_workload_version: str,
) -> bool:
    """Check if the workload versions are compatible."""
    try:
        old_major, old_minor, old_patch, *_ = (
            int(component) for component in old_workload_version.split(".")
        )
        new_major, new_minor, new_patch, *_ = (
            int(component) for component in new_workload_version.split(".")
        )
    except ValueError:
        # Not enough values to unpack or cannot convert
        logger.info(
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
        # todo: also allow new_minor == old_minor + 1
        # if base stays the same and for specific revisions
        logger.info(
            "Refreshing to a different minor version workload is not supported. "
            f"Got {old_major}.{old_minor}.{old_patch} to {new_major}.{new_minor}.{new_patch}"
        )
        return False

    if not new_patch >= old_patch:
        logger.info(
            "Downgrading to a previous patch version workload is not supported. "
            f"Got {old_major}.{old_minor}.{old_patch} to {new_major}.{new_minor}.{new_patch}"
        )

    return True
