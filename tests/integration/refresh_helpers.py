# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper functions for charm-refresh integration tests."""

import asyncio
import logging
from typing import Dict, List, Optional

from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    get_primary_unit,
    wait_until,
)

logger = logging.getLogger(__name__)

# Charm refresh specific constants
REFRESH_CHARM_NAME = "charmed-etcd"
REFRESH_WORKLOAD_NAME = "etcd"
DEFAULT_SNAP_NAME = "charmed-etcd"

# Timeout constants for refresh operations
REFRESH_TIMEOUT = 600  # 10 minutes for refresh operations
HEALTH_CHECK_TIMEOUT = 300  # 5 minutes for health checks
SNAP_REFRESH_TIMEOUT = 180  # 3 minutes for snap refresh


async def wait_for_refresh_to_start(
    ops_test: OpsTest, app_name: str, timeout: int = REFRESH_TIMEOUT
) -> None:
    """Wait for refresh to start on the application.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        timeout: Maximum time to wait in seconds.
    """

    async def _refresh_started():
        """Check if refresh has started."""
        try:
            # Get the primary unit to check refresh status
            primary_unit = await get_primary_unit(ops_test, app_name)

            # Run action to check refresh status
            action = await primary_unit.run_action("get-primary")
            await action.wait()

            # Check if refresh is in progress via unit status or other indicators
            status = ops_test.model.applications[app_name].status
            return "refresh" in status.lower() or "upgrading" in status.lower()
        except Exception as e:
            logger.debug(f"Error checking refresh status: {e}")
            return False

    await wait_until(
        ops_test,
        _refresh_started,
        timeout=timeout,
        check_interval=10,
    )


async def wait_for_refresh_to_complete(
    ops_test: OpsTest, app_name: str, timeout: int = REFRESH_TIMEOUT
) -> None:
    """Wait for refresh to complete on all units.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        timeout: Maximum time to wait in seconds.
    """

    async def _refresh_completed():
        """Check if refresh has completed on all units."""
        try:
            app = ops_test.model.applications[app_name]

            # Check that all units are active/idle
            for unit in app.units:
                if unit.workload_status != "active":
                    logger.debug(f"Unit {unit.name} status: {unit.workload_status}")
                    return False
                if unit.agent_status != "idle":
                    logger.debug(f"Unit {unit.name} agent status: {unit.agent_status}")
                    return False

            # Check that cluster is healthy
            primary_unit = await get_primary_unit(ops_test, app_name)
            action = await primary_unit.run_action("get-primary")
            result = await action.wait()

            if "failed" in result.status:
                logger.debug(f"Primary check failed: {result.message}")
                return False

            return True
        except Exception as e:
            logger.debug(f"Error checking refresh completion: {e}")
            return False

    await wait_until(
        ops_test,
        _refresh_completed,
        timeout=timeout,
        check_interval=15,
    )


async def check_cluster_health_during_refresh(
    ops_test: OpsTest, app_name: str, check_duration: int = 60
) -> bool:
    """Check cluster health continuously during refresh.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        check_duration: How long to check in seconds.

    Returns:
        True if cluster remained healthy, False otherwise.
    """
    start_time = asyncio.get_event_loop().time()
    unhealthy_periods = []

    while (asyncio.get_event_loop().time() - start_time) < check_duration:
        try:
            primary_unit = await get_primary_unit(ops_test, app_name)
            action = await primary_unit.run_action("get-primary")
            result = await action.wait()

            if "failed" in result.status:
                unhealthy_periods.append(asyncio.get_event_loop().time())
                logger.warning(f"Cluster unhealthy at {asyncio.get_event_loop().time()}")

        except Exception as e:
            unhealthy_periods.append(asyncio.get_event_loop().time())
            logger.warning(f"Health check failed at {asyncio.get_event_loop().time()}: {e}")

        await asyncio.sleep(5)

    # Allow for brief unhealthy periods during refresh
    max_allowed_unhealthy_time = 30  # seconds
    total_unhealthy_time = len(unhealthy_periods) * 5  # 5 second intervals

    logger.info(f"Total unhealthy time during refresh: {total_unhealthy_time}s")
    return total_unhealthy_time <= max_allowed_unhealthy_time


async def get_snap_revision(ops_test: OpsTest, unit_name: str, snap_name: str) -> str:
    """Get the current snap revision for a unit.

    Args:
        ops_test: The ops test framework instance.
        unit_name: Name of the unit to check.
        snap_name: Name of the snap.

    Returns:
        Current snap revision as string.
    """
    unit = ops_test.model.units[unit_name]

    result = await unit.run(f"snap list {snap_name}")
    if result.return_code != 0:
        raise RuntimeError(f"Failed to get snap info: {result.stderr}")

    # Parse snap list output to get revision
    lines = result.stdout.strip().split("\n")
    for line in lines[1:]:  # Skip header
        parts = line.split()
        if parts[0] == snap_name:
            return parts[2]  # Revision is typically in the 3rd column

    raise RuntimeError(f"Snap {snap_name} not found on unit {unit_name}")


async def get_all_snap_revisions(
    ops_test: OpsTest, app_name: str, snap_name: str
) -> Dict[str, str]:
    """Get snap revisions for all units in an application.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        snap_name: Name of the snap.

    Returns:
        Dictionary mapping unit names to snap revisions.
    """
    app = ops_test.model.applications[app_name]
    revisions = {}

    for unit in app.units:
        try:
            revision = await get_snap_revision(ops_test, unit.name, snap_name)
            revisions[unit.name] = revision
        except Exception as e:
            logger.error(f"Failed to get snap revision for {unit.name}: {e}")
            revisions[unit.name] = "unknown"

    return revisions


async def trigger_refresh_action(
    ops_test: OpsTest, app_name: str, action_name: str, parameters: Optional[Dict] = None
) -> Dict:
    """Trigger a refresh action on the application.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        action_name: Name of the action to run.
        parameters: Optional parameters for the action.

    Returns:
        Action result.
    """
    app = ops_test.model.applications[app_name]
    leader_unit = None

    # Find the leader unit
    for unit in app.units:
        if await unit.is_leader_from_status():
            leader_unit = unit
            break

    if not leader_unit:
        raise RuntimeError(f"No leader unit found for {app_name}")

    logger.info(f"Running action {action_name} on {leader_unit.name}")
    action = await leader_unit.run_action(action_name, **parameters or {})
    result = await action.wait()

    logger.info(f"Action {action_name} result: {result.status}")
    if result.message:
        logger.info(f"Action message: {result.message}")

    return result


async def run_pre_refresh_check(ops_test: OpsTest, app_name: str) -> Dict:
    """Run pre-refresh-check action.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.

    Returns:
        Action result.
    """
    return await trigger_refresh_action(ops_test, app_name, "pre-refresh-check")


async def force_refresh_start(
    ops_test: OpsTest,
    app_name: str,
    check_compatibility: bool = True,
    run_pre_refresh_checks: bool = True,
    check_workload_container: bool = True,
) -> Dict:
    """Force refresh start with specified parameters.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        check_compatibility: Whether to check compatibility.
        run_pre_refresh_checks: Whether to run pre-refresh checks.
        check_workload_container: Whether to check workload container.

    Returns:
        Action result.
    """
    parameters = {
        "check-compatibility": check_compatibility,
        "run-pre-refresh-checks": run_pre_refresh_checks,
        "check-workload-container": check_workload_container,
    }

    return await trigger_refresh_action(ops_test, app_name, "force-refresh-start", parameters)


async def resume_refresh(
    ops_test: OpsTest, app_name: str, check_health_of_refreshed_units: bool = True
) -> Dict:
    """Resume refresh with specified parameters.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        check_health_of_refreshed_units: Whether to check health of refreshed units.

    Returns:
        Action result.
    """
    parameters = {
        "check-health-of-refreshed-units": check_health_of_refreshed_units,
    }

    return await trigger_refresh_action(ops_test, app_name, "resume-refresh", parameters)


async def verify_cluster_data_integrity(
    ops_test: OpsTest,
    app_name: str,
    test_key: str = "refresh-test-key",
    test_value: str = "refresh-test-value",
) -> bool:
    """Verify that data is preserved during refresh.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        test_key: Key to use for testing.
        test_value: Value to use for testing.

    Returns:
        True if data integrity is verified, False otherwise.
    """
    try:
        primary_unit = await get_primary_unit(ops_test, app_name)

        # Write test data
        result = await primary_unit.run(f"etcdctl put {test_key} {test_value}")
        if result.return_code != 0:
            logger.error(f"Failed to write test data: {result.stderr}")
            return False

        # Read test data back
        result = await primary_unit.run(f"etcdctl get {test_key}")
        if result.return_code != 0:
            logger.error(f"Failed to read test data: {result.stderr}")
            return False

        # Verify the value
        output_lines = result.stdout.strip().split("\n")
        if len(output_lines) >= 2 and output_lines[1] == test_value:
            logger.info("Data integrity verified")
            return True
        else:
            logger.error(
                f"Data integrity check failed. Expected: {test_value}, Got: {output_lines}"
            )
            return False

    except Exception as e:
        logger.error(f"Error verifying data integrity: {e}")
        return False


async def simulate_workload_during_refresh(
    ops_test: OpsTest, app_name: str, duration: int = 60, operation_interval: int = 5
) -> List[bool]:
    """Simulate continuous workload during refresh.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        duration: How long to run the workload in seconds.
        operation_interval: Interval between operations in seconds.

    Returns:
        List of success/failure results for each operation.
    """
    results = []
    start_time = asyncio.get_event_loop().time()
    operation_count = 0

    while (asyncio.get_event_loop().time() - start_time) < duration:
        try:
            primary_unit = await get_primary_unit(ops_test, app_name)

            # Perform a simple etcd operation
            test_key = f"workload-test-{operation_count}"
            test_value = f"value-{operation_count}"

            result = await primary_unit.run(f"etcdctl put {test_key} {test_value}")

            success = result.return_code == 0
            results.append(success)

            if not success:
                logger.warning(f"Operation {operation_count} failed: {result.stderr}")
            else:
                logger.debug(f"Operation {operation_count} succeeded")

        except Exception as e:
            logger.warning(f"Operation {operation_count} failed with exception: {e}")
            results.append(False)

        operation_count += 1
        await asyncio.sleep(operation_interval)

    success_rate = sum(results) / len(results) if results else 0
    logger.info(f"Workload simulation completed. Success rate: {success_rate:.2%}")

    return results


async def check_refresh_config_options(ops_test: OpsTest, app_name: str) -> Dict:
    """Check the refresh-related configuration options.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.

    Returns:
        Dictionary of refresh configuration options.
    """
    app = ops_test.model.applications[app_name]
    config = await app.get_config()

    refresh_config = {}

    # Check for pause_after_unit_refresh option
    if "pause_after_unit_refresh" in config:
        refresh_config["pause_after_unit_refresh"] = config["pause_after_unit_refresh"]["value"]

    logger.info(f"Refresh configuration: {refresh_config}")
    return refresh_config


async def set_refresh_config(ops_test: OpsTest, app_name: str, config: Dict[str, str]) -> None:
    """Set refresh-related configuration options.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        config: Configuration options to set.
    """
    app = ops_test.model.applications[app_name]
    await app.set_config(config)

    # Wait for config change to be applied
    await ops_test.model.wait_for_idle(
        apps=[app_name],
        status="active",
        timeout=300,
    )


class RefreshTestContext:
    """Context manager for refresh testing."""

    def __init__(self, ops_test: OpsTest, app_name: str, verify_data_integrity: bool = True):
        """Initialize refresh test context.

        Args:
            ops_test: The ops test framework instance.
            app_name: Name of the application.
            verify_data_integrity: Whether to verify data integrity.
        """
        self.ops_test = ops_test
        self.app_name = app_name
        self.verify_data_integrity = verify_data_integrity
        self.initial_snap_revisions = {}
        self.test_data_key = "refresh-context-test"
        self.test_data_value = f"test-value-{asyncio.get_event_loop().time()}"

    async def __aenter__(self):
        """Enter the refresh test context."""
        logger.info(f"Starting refresh test context for {self.app_name}")

        # Record initial snap revisions
        self.initial_snap_revisions = await get_all_snap_revisions(
            self.ops_test, self.app_name, DEFAULT_SNAP_NAME
        )
        logger.info(f"Initial snap revisions: {self.initial_snap_revisions}")

        # Set up test data if verification is enabled
        if self.verify_data_integrity:
            await verify_cluster_data_integrity(
                self.ops_test, self.app_name, self.test_data_key, self.test_data_value
            )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the refresh test context."""
        logger.info(f"Ending refresh test context for {self.app_name}")

        # Verify data integrity if enabled
        if self.verify_data_integrity:
            integrity_ok = await verify_cluster_data_integrity(
                self.ops_test, self.app_name, self.test_data_key, self.test_data_value
            )
            if not integrity_ok:
                logger.error("Data integrity verification failed!")

        # Log final snap revisions
        final_snap_revisions = await get_all_snap_revisions(
            self.ops_test, self.app_name, DEFAULT_SNAP_NAME
        )
        logger.info(f"Final snap revisions: {final_snap_revisions}")

        # Check for any changes
        changed_units = []
        for unit_name in self.initial_snap_revisions:
            if (
                unit_name in final_snap_revisions
                and self.initial_snap_revisions[unit_name] != final_snap_revisions[unit_name]
            ):
                changed_units.append(unit_name)

        if changed_units:
            logger.info(f"Units with changed snap revisions: {changed_units}")
        else:
            logger.info("No snap revisions changed during test")


# Utility functions for refresh testing


def create_refresh_versions_toml(
    charm_major: int = 1,
    workload_version: str = "3.5.18",
    snap_name: str = DEFAULT_SNAP_NAME,
    x86_64_revision: str = "13",
    aarch64_revision: str = "16",
) -> str:
    """Create a refresh_versions.toml content for testing.

    Args:
        charm_major: Major version of the charm.
        workload_version: Version of the workload.
        snap_name: Name of the snap.
        x86_64_revision: Snap revision for x86_64 architecture.
        aarch64_revision: Snap revision for aarch64 architecture.

    Returns:
        TOML content as string.
    """
    return f"""[charm]
major = {charm_major}

[workload]
version = "{workload_version}"

[snaps.{snap_name}]
[snaps.{snap_name}.revisions]
x86_64 = "{x86_64_revision}"
aarch64 = "{aarch64_revision}"
"""


async def wait_for_refresh_status(
    ops_test: OpsTest, app_name: str, expected_status: str, timeout: int = 300
) -> None:
    """Wait for a specific refresh status.

    Args:
        ops_test: The ops test framework instance.
        app_name: Name of the application.
        expected_status: Expected status string to wait for.
        timeout: Maximum time to wait in seconds.
    """

    async def _status_matches():
        """Check if status matches expected."""
        try:
            app = ops_test.model.applications[app_name]
            current_status = app.status.lower()
            return expected_status.lower() in current_status
        except Exception as e:
            logger.debug(f"Error checking status: {e}")
            return False

    await wait_until(
        ops_test,
        _status_matches,
        timeout=timeout,
        check_interval=5,
    )
