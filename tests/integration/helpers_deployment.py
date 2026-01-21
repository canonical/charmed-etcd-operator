#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from datetime import datetime, timedelta

import jubilant
from data_platform_helpers.advanced_statuses.models import StatusObject
from dateutil.parser import parse
from jubilant.statustypes import StatusInfo
from ops import StatusBase

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(filename)s:%(lineno)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


class ExpectedStatus:
    """Model class for an ExpectedStatus.

    Describes expected app status(es), unit status(es), unit count and idle period, for a given app.
    """

    def __init__(
        self,
        app_status: list[StatusObject | str] | None = None,
        unit_status: list[StatusObject | str] | None = None,
        unit_count: int | None = None,
        idle_period: int | None = None,
    ):
        self.app_status = app_status
        self.unit_status = unit_status
        self.unit_count = unit_count
        self.idle_period = idle_period


def apps_active_and_agents_idle(
    status: jubilant.Status,
    *apps: str,
    idle_period: int = 0,
    unit_count: int | dict[str, int] = None,
) -> bool:
    """Check that all given apps are active, their agents idle (optional idle interval too) and optionally verify unit count as well.

    Args:
        status: represents the jubilant model's current status
        apps: A list of applications whose statuses to test against
        idle_period: Seconds to wait for the agents of each application unit to be idle.
        unit_count: The desired number of units to wait for, can be > to 0.
            If set as int, this value is expected for all apps but if more granularity is needed,
            pass a dictionary such as: {"app1": 2, "app2": 1, ...}, if set to -1, the check
            only happens at the application level.
    """
    return (
        jubilant.all_active(status, *apps)
        and jubilant.all_agents_idle(status, *apps)
        and _check_apps_idle_period(status, *apps, idle_period=idle_period)
        and _verify_unit_count(status, *apps, unit_count=unit_count)
    )


def agents_idle(
    status: jubilant.Status,
    *apps: str,
    idle_period: int = 0,
    unit_count: int | dict[str, int] = None,
) -> bool:
    """Check that agents of all given apps are idle (optional idle interval too). Optionally verify unit count as well.

    Args:
        status: represents the jubilant model's current status
        apps: A list of applications whose statuses to test against
        idle_period: Seconds to wait for the agents of each application unit to be idle.
        unit_count: The desired number of units to wait for, should be > 0.
            If set as int, this value is expected for all apps but if more granularity is needed,
            pass a dictionary such as: {"app1": 2, "app2": 1, ...}, if set to -1, the check
            only happens at the application level.
    """
    return (
        jubilant.all_agents_idle(status, *apps)
        and _check_apps_idle_period(status, *apps, idle_period=idle_period)
        and _verify_unit_count(status, *apps, unit_count=unit_count)
    )


def _check_apps_idle_period(status: jubilant.Status, *apps: str, idle_period: int) -> bool:
    return all(
        parse(unit.juju_status.since, ignoretz=True) + timedelta(seconds=idle_period)
        < datetime.now()
        for app in apps
        for unit in status.get_units(app).values()
    )


def _verify_unit_count(
    status: jubilant.Status, *apps: str, unit_count: int | dict[str, int] = None
):
    """Verify the unit count for an application.

    Args:
        status: represents the jubilant model's current status
        apps: A list of applications whose statuses to test against
        unit_count: The desired number of units to wait for, can be >= to -1
            if set as int, this value is expected for all apps but if more granularity is needed,
            pass a dictionary such as: {"app1": 2, "app2": 1, ...}, if set to -1, the check
            only happens at the application level.
    """
    if not unit_count:
        return True

    if isinstance(unit_count, int):
        if unit_count == 0:
            return True
        unit_count = dict.fromkeys(apps, unit_count)
    elif not unit_count:
        unit_count = dict.fromkeys(apps, -1)
    else:
        for app in apps:
            if app not in unit_count:
                unit_count[app] = 1

    return all(count == len(status.get_units(app)) for app, count in unit_count.items())


def does_status_match(
    model_status: jubilant.Status, expected_status: dict[str, ExpectedStatus]
) -> bool:
    """Check that current app and/or unit status matches expectation for given apps.

    Args:
        model_status: represents the jubilant model's current status
        expected_status: dict mapping app name to its ExpectedStatus
    """
    return all(
        (
            expected_status.unit_status is None
            or _does_unit_workload_status_match(model_status, app, expected_status.unit_status)
        )
        and (
            expected_status.app_status is None
            or _does_app_status_match(model_status, app, expected_status.app_status)
        )
        and (
            expected_status.unit_count is None
            or _verify_unit_count(model_status, app, unit_count=expected_status.unit_count)
        )
        and (
            expected_status.idle_period is None
            or _check_apps_idle_period(model_status, app, idle_period=expected_status.idle_period)
        )
        for app, expected_status in expected_status.items()
    )


def _does_unit_workload_status_match(
    model_status: jubilant.Status, app: str, expected_status: list[StatusObject | str]
) -> bool:
    """Check that current workload status matches expectation for given apps' units.

    Args:
        model_status: represents the jubilant model's current status
        app: name of the app whose status is to be checked
        expected_status: list of acceptable statuses
    """
    return all(
        any(_does_message_match(unit_status.workload_status, status) for status in expected_status)
        for unit_status in model_status.get_units(app).values()
    )


def _does_app_status_match(
    model_status: jubilant.Status, app: str, expected_status: list[StatusObject | str]
) -> bool:
    """Check that current app status matches expectation for given apps.

    Args:
        model_status: represents the jubilant model's current status
        app: name of the app whose status is to be checked
        expected_status: list of acceptable statuses
    """
    return any(
        _does_message_match(model_status.apps.get(app).app_status, status)
        for status in expected_status
    )


def _does_message_match(model_status: StatusInfo, expected_status: StatusObject | str) -> bool:
    """Check if the status message matches the expected message."""
    if isinstance(expected_status, StatusObject):
        try:
            juju_status = StatusBase.from_name(expected_status.status, expected_status.message)
            current_status = model_status.message
            return (
                current_status == juju_status.message
                or current_status.startswith(juju_status.message)
                or current_status.startswith(f"{juju_status.message:.40}")
                or (
                    expected_status.short_message is not None
                    and expected_status.short_message in current_status
                )
            )
        except KeyError as e:
            logger.error(f"Error attempting to convert StatusObject to ops.StatusBase: {e}")
            return False
    else:
        return model_status.current == expected_status
