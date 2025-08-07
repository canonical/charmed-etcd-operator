#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for computing upgrades related statuses."""

import logging

import charm_refresh
import ops
from data_platform_helpers.advanced_statuses.models import StatusObject
from data_platform_helpers.advanced_statuses.protocol import ManagerStatusProtocol
from data_platform_helpers.advanced_statuses.types import Scope
from ops import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus

from core.workload import WorkloadBase
from statuses import CharmStatuses

logger = logging.getLogger(__name__)


class UpgradesManager(ManagerStatusProtocol):
    """Manage upgrades statuses, but nothing else."""

    name: str = "upgrades"

    def __init__(self, workload: WorkloadBase, refresh: charm_refresh.Machines):
        self.workload = workload
        self.refresh = refresh

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the upgrades-relevant statuses.

        Advanced Statuses defines the status priority order per component. It is not possible to
        have some statuses of a component more important and some other statuses of the same
        component less important.

        While `refresh.[app|unit]_status_higher_priority` must be of higher priority than any other
        status, `refresh.unit_status_lower_priority()` should only be set it if there is no other
        status at all. We achieve this by having `upgrades_manager` last in the component priority
        order, but setting the field `approved_critical_component` to True if the refresh_status is
        of higher priority than any other status.

        This logic ignores all statuses set directly by the refresh lib, as of refresh v3.1.0.
        For more information: see https://canonical-charm-refresh.readthedocs-hosted.com/latest/add-to-charm/status/
        """
        status_list: list[StatusObject] = []

        if self.refresh is None:
            return [CharmStatuses.ACTIVE_IDLE.value]

        if refresh_app_status := self.refresh.app_status_higher_priority is not None:
            try:
                app_status = self._convert_ops_status_to_advanced_status(refresh_app_status)
                status_list.append(app_status)
            except ValueError as e:
                logger.error(e)

        if refresh_unit_status := self.refresh.unit_status_higher_priority is not None:
            try:
                unit_status = self._convert_ops_status_to_advanced_status(refresh_unit_status)
                status_list.append(unit_status)
            except ValueError as e:
                logger.error(e)

        if (
            refresh_lower_unit_status := self.refresh.unit_status_lower_priority(
                workload_is_running=self.workload.alive()
            )
            is not None
        ):
            try:
                lower_unit_status = self._convert_ops_status_to_advanced_status(
                    refresh_lower_unit_status, critical=False
                )
                status_list.append(lower_unit_status)
            except ValueError as e:
                logger.error(e)

        return status_list if status_list else [CharmStatuses.ACTIVE_IDLE.value]

    @staticmethod
    def _convert_ops_status_to_advanced_status(
        ops_status: ops.StatusBase, critical: bool = True
    ) -> StatusObject:
        """Convert an ops status into an advanced statuses StatusObject.

        Args:
            ops_status (ops.StatusBase): the status to convert into an advanced status
            critical (bool): whether the returned StatusObject should have the field
                            `approved_critical_component` set to True or False
        """
        # this code may not be very concise, focus is on readability
        if isinstance(ops_status, BlockedStatus):
            return StatusObject(
                status="blocked", message=ops_status.message, approved_critical_component=critical
            )

        if isinstance(ops_status, MaintenanceStatus):
            return StatusObject(
                status="maintenance",
                message=ops_status.message,
                approved_critical_component=critical,
            )

        if isinstance(ops_status, WaitingStatus):
            return StatusObject(
                status="waiting", message=ops_status.message, approved_critical_component=critical
            )

        if isinstance(ops_status, ActiveStatus):
            return StatusObject(status="active", message=ops_status.message)

        raise ValueError(f"Unknown status type: {ops_status.name}: {ops_status.message}")
