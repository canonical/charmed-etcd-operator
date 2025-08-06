#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all upgrades related tasks."""

import logging

import charm_refresh
from data_platform_helpers.advanced_statuses.models import StatusObject
from data_platform_helpers.advanced_statuses.protocol import ManagerStatusProtocol
from data_platform_helpers.advanced_statuses.types import Scope
from ops import BlockedStatus

from core.workload import WorkloadBase
from statuses import CharmStatuses

logger = logging.getLogger(__name__)


class UpgradesManager(ManagerStatusProtocol):
    """Manage upgrades."""

    name: str = "upgrades"

    def __init__(self, workload: WorkloadBase, refresh: charm_refresh.Machines):
        self.workload = workload
        self.refresh = refresh

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the Cluster manager's statuses."""
        status_list: list[StatusObject] = []

        if self.refresh is None:
            return [CharmStatuses.ACTIVE_IDLE.value]

        if refresh_app_status := self.refresh.app_status_higher_priority is not None:
            if isinstance(refresh_app_status, BlockedStatus):
                # todo: instantiate StatusObject and append to list
                logger.debug(f"{refresh_app_status.message}")

        if refresh_unit_status := self.refresh.unit_status_higher_priority is not None:
            if isinstance(refresh_unit_status, BlockedStatus):
                # todo: rework
                unit_status = StatusObject(status="blocked", message=refresh_unit_status.message)
                status_list.append(unit_status)

        if refresh_unit_status_low := self.refresh.unit_status_lower_priority(workload_is_running=self.workload.alive()) is not None:
            if isinstance(refresh_unit_status_low, BlockedStatus):
                logger.debug(f"{refresh_unit_status_low.message}")

        return status_list if status_list else [CharmStatuses.ACTIVE_IDLE.value]
