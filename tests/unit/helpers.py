#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from data_platform_helpers.advanced_statuses.models import StatusObject
from data_platform_helpers.advanced_statuses.utils import as_status
from scenario import State


def status_is(state_out: State, to_status: StatusObject, is_app: bool = False) -> bool:
    """Check if the status is set to the given status."""
    status = state_out.app_status if is_app else state_out.unit_status
    juju_status = as_status(to_status)
    return status.name == juju_status.name and (
        status.message == juju_status.message
        or status.message.startswith(juju_status.message)
        or juju_status.message.startswith(f"{status.message:.40}")
        or (to_status.short_message is not None and to_status.short_message in status.message)
    )
