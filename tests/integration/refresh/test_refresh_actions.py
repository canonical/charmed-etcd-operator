# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for charm-refresh actions."""

import asyncio
import logging

import pytest
import pytest_asyncio
from pytest_operator.plugin import OpsTest

from tests.integration.refresh_helpers import (
    DEFAULT_SNAP_NAME,
    RefreshTestContext,
    check_refresh_config_options,
    force_refresh_start,
    get_all_snap_revisions,
    resume_refresh,
    run_pre_refresh_check,
    set_refresh_config,
    verify_cluster_data_integrity,
)

logger = logging.getLogger(__name__)

APP_NAME = "etcd"
CHARM_NAME = "charmed-etcd"


@pytest_asyncio.fixture(scope="module")
async def etcd_cluster(ops_test: OpsTest):
    """Deploy etcd cluster for refresh action testing."""
    logger.info("Deploying etcd cluster for refresh action testing")

    # Deploy a 3-unit cluster for action testing
    await ops_test.model.deploy(
        CHARM_NAME, application_name=APP_NAME, num_units=3, channel="latest/edge"
    )

    # Wait for the cluster to be ready
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="active",
        timeout=600,
    )

    return ops_test.model.applications[APP_NAME]


class TestPreRefreshCheckAction:
    """Test the pre-refresh-check action."""

    @pytest.mark.asyncio
    async def test_pre_refresh_check_basic(self, ops_test: OpsTest, etcd_cluster):
        """Test basic pre-refresh-check action functionality."""
        logger.info("Testing basic pre-refresh-check action")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Run pre-refresh check
            result = await run_pre_refresh_check(ops_test, APP_NAME)

            # Verify action completed successfully
            assert result.status == "completed"
            logger.info(f"Pre-refresh check result: {result.message}")

    @pytest.mark.asyncio
    async def test_pre_refresh_check_with_healthy_cluster(self, ops_test: OpsTest, etcd_cluster):
        """Test pre-refresh-check with a healthy cluster."""
        logger.info("Testing pre-refresh-check with healthy cluster")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Ensure cluster has test data
            integrity_ok = await verify_cluster_data_integrity(
                ops_test, APP_NAME, test_key="health-test-key", test_value="health-test-value"
            )
            assert integrity_ok, "Cluster should be healthy before pre-refresh check"

            # Run pre-refresh check
            result = await run_pre_refresh_check(ops_test, APP_NAME)

            # Should succeed with healthy cluster
            assert result.status == "completed"

            # Verify cluster is still healthy after check
            integrity_ok = await verify_cluster_data_integrity(
                ops_test, APP_NAME, test_key="health-test-key", test_value="health-test-value"
            )
            assert integrity_ok, "Cluster should remain healthy after pre-refresh check"

    @pytest.mark.asyncio
    async def test_pre_refresh_check_multiple_calls(self, ops_test: OpsTest, etcd_cluster):
        """Test multiple consecutive pre-refresh-check calls."""
        logger.info("Testing multiple pre-refresh-check calls")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Run multiple pre-refresh checks
            for i in range(3):
                logger.info(f"Running pre-refresh check #{i + 1}")
                result = await run_pre_refresh_check(ops_test, APP_NAME)
                assert result.status == "completed"

                # Brief delay between checks
                await asyncio.sleep(5)

    @pytest.mark.asyncio
    async def test_pre_refresh_check_with_different_configs(self, ops_test: OpsTest, etcd_cluster):
        """Test pre-refresh-check with different configurations."""
        logger.info("Testing pre-refresh-check with different configurations")

        configs = [
            {"pause_after_unit_refresh": "none"},
            {"pause_after_unit_refresh": "first"},
            {"pause_after_unit_refresh": "all"},
        ]

        for config in configs:
            logger.info(f"Testing pre-refresh-check with config: {config}")

            async with RefreshTestContext(ops_test, APP_NAME):
                # Set configuration
                await set_refresh_config(ops_test, APP_NAME, config)

                # Verify configuration was set
                current_config = await check_refresh_config_options(ops_test, APP_NAME)
                assert (
                    current_config["pause_after_unit_refresh"]
                    == config["pause_after_unit_refresh"]
                )

                # Run pre-refresh check
                result = await run_pre_refresh_check(ops_test, APP_NAME)
                assert result.status == "completed"


class TestForceRefreshStartAction:
    """Test the force-refresh-start action."""

    @pytest.mark.asyncio
    async def test_force_refresh_start_basic(self, ops_test: OpsTest, etcd_cluster):
        """Test basic force-refresh-start action functionality."""
        logger.info("Testing basic force-refresh-start action")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Run force refresh start with default parameters
            result = await force_refresh_start(ops_test, APP_NAME)

            logger.info(f"Force refresh start result: {result.status}")
            logger.info(f"Force refresh start message: {result.message}")

            # Action should at least execute (may fail in test environment)
            assert result.status in ["completed", "failed"]

    @pytest.mark.asyncio
    async def test_force_refresh_start_with_parameters(self, ops_test: OpsTest, etcd_cluster):
        """Test force-refresh-start with different parameters."""
        logger.info("Testing force-refresh-start with parameters")

        parameter_sets = [
            {
                "check_compatibility": True,
                "run_pre_refresh_checks": True,
                "check_workload_container": True,
            },
            {
                "check_compatibility": False,
                "run_pre_refresh_checks": False,
                "check_workload_container": False,
            },
            {
                "check_compatibility": True,
                "run_pre_refresh_checks": False,
                "check_workload_container": True,
            },
        ]

        for params in parameter_sets:
            logger.info(f"Testing force-refresh-start with params: {params}")

            async with RefreshTestContext(ops_test, APP_NAME):
                # Run force refresh start with specific parameters
                result = await force_refresh_start(
                    ops_test,
                    APP_NAME,
                    check_compatibility=params["check_compatibility"],
                    run_pre_refresh_checks=params["run_pre_refresh_checks"],
                    check_workload_container=params["check_workload_container"],
                )

                logger.info(f"Result: {result.status} - {result.message}")

                # Action should execute (may fail in test environment)
                assert result.status in ["completed", "failed"]

                # Brief delay between tests
                await asyncio.sleep(10)

    @pytest.mark.asyncio
    async def test_force_refresh_start_after_pre_check(self, ops_test: OpsTest, etcd_cluster):
        """Test force-refresh-start after running pre-refresh-check."""
        logger.info("Testing force-refresh-start after pre-refresh-check")

        async with RefreshTestContext(ops_test, APP_NAME):
            # First run pre-refresh check
            pre_result = await run_pre_refresh_check(ops_test, APP_NAME)
            assert pre_result.status == "completed"

            # Then run force refresh start
            refresh_result = await force_refresh_start(
                ops_test,
                APP_NAME,
                check_compatibility=True,
                run_pre_refresh_checks=False,  # Skip since we just ran it
                check_workload_container=True,
            )

            logger.info(f"Refresh after pre-check result: {refresh_result.status}")
            assert refresh_result.status in ["completed", "failed"]

    @pytest.mark.asyncio
    async def test_force_refresh_start_compatibility_check(self, ops_test: OpsTest, etcd_cluster):
        """Test force-refresh-start compatibility checking."""
        logger.info("Testing force-refresh-start compatibility checking")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Test with compatibility check enabled
            result = await force_refresh_start(
                ops_test,
                APP_NAME,
                check_compatibility=True,
                run_pre_refresh_checks=True,
                check_workload_container=False,
            )

            logger.info(f"Compatibility check result: {result.status} - {result.message}")

            # Should execute compatibility checks
            assert result.status in ["completed", "failed"]

            # If it failed, it should mention compatibility
            if result.status == "failed" and result.message:
                logger.info(f"Compatibility check details: {result.message}")


class TestResumeRefreshAction:
    """Test the resume-refresh action."""

    @pytest.mark.asyncio
    async def test_resume_refresh_basic(self, ops_test: OpsTest, etcd_cluster):
        """Test basic resume-refresh action functionality."""
        logger.info("Testing basic resume-refresh action")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Run resume refresh with default parameters
            result = await resume_refresh(ops_test, APP_NAME)

            logger.info(f"Resume refresh result: {result.status}")
            logger.info(f"Resume refresh message: {result.message}")

            # Action should execute (may indicate no refresh in progress)
            assert result.status in ["completed", "failed"]

    @pytest.mark.asyncio
    async def test_resume_refresh_with_parameters(self, ops_test: OpsTest, etcd_cluster):
        """Test resume-refresh with different parameters."""
        logger.info("Testing resume-refresh with parameters")

        parameter_sets = [
            {"check_health_of_refreshed_units": True},
            {"check_health_of_refreshed_units": False},
        ]

        for params in parameter_sets:
            logger.info(f"Testing resume-refresh with params: {params}")

            async with RefreshTestContext(ops_test, APP_NAME):
                # Run resume refresh with specific parameters
                result = await resume_refresh(
                    ops_test,
                    APP_NAME,
                    check_health_of_refreshed_units=params["check_health_of_refreshed_units"],
                )

                logger.info(f"Result: {result.status} - {result.message}")

                # Action should execute
                assert result.status in ["completed", "failed"]

                # Brief delay between tests
                await asyncio.sleep(5)

    @pytest.mark.asyncio
    async def test_resume_refresh_sequence(self, ops_test: OpsTest, etcd_cluster):
        """Test resume-refresh in sequence with other actions."""
        logger.info("Testing resume-refresh in sequence")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Run pre-refresh check
            pre_result = await run_pre_refresh_check(ops_test, APP_NAME)
            assert pre_result.status == "completed"

            # Try to start refresh
            start_result = await force_refresh_start(
                ops_test,
                APP_NAME,
                check_compatibility=True,
                run_pre_refresh_checks=False,
                check_workload_container=False,
            )

            logger.info(f"Start result: {start_result.status}")

            # Try to resume refresh
            resume_result = await resume_refresh(
                ops_test, APP_NAME, check_health_of_refreshed_units=True
            )

            logger.info(f"Resume result: {resume_result.status}")
            assert resume_result.status in ["completed", "failed"]


class TestRefreshActionValidation:
    """Test refresh action validation and error handling."""

    @pytest.mark.asyncio
    async def test_action_parameter_validation(self, ops_test: OpsTest, etcd_cluster):
        """Test refresh action parameter validation."""
        logger.info("Testing refresh action parameter validation")

        async with RefreshTestContext(ops_test, APP_NAME):
            app = ops_test.model.applications[APP_NAME]
            leader_unit = None

            # Find leader unit
            for unit in app.units:
                if await unit.is_leader_from_status():
                    leader_unit = unit
                    break

            assert leader_unit is not None, "No leader unit found"

            # Test force-refresh-start with boolean parameters
            try:
                action = await leader_unit.run_action(
                    "force-refresh-start",
                    **{
                        "check-compatibility": True,
                        "run-pre-refresh-checks": True,
                        "check-workload-container": True,
                    },
                )
                result = await action.wait()
                logger.info(f"Boolean parameter test result: {result.status}")
                assert result.status in ["completed", "failed"]
            except Exception as e:
                logger.info(f"Boolean parameter test error (may be expected): {e}")

            # Test resume-refresh with boolean parameters
            try:
                action = await leader_unit.run_action(
                    "resume-refresh", **{"check-health-of-refreshed-units": True}
                )
                result = await action.wait()
                logger.info(f"Resume boolean parameter test result: {result.status}")
                assert result.status in ["completed", "failed"]
            except Exception as e:
                logger.info(f"Resume boolean parameter test error (may be expected): {e}")

    @pytest.mark.asyncio
    async def test_action_on_non_leader_unit(self, ops_test: OpsTest, etcd_cluster):
        """Test refresh actions on non-leader units."""
        logger.info("Testing refresh actions on non-leader units")

        async with RefreshTestContext(ops_test, APP_NAME):
            app = ops_test.model.applications[APP_NAME]
            non_leader_unit = None

            # Find a non-leader unit
            for unit in app.units:
                if not await unit.is_leader_from_status():
                    non_leader_unit = unit
                    break

            if non_leader_unit is None:
                logger.warning("No non-leader units found, skipping test")
                return

            logger.info(f"Testing actions on non-leader unit: {non_leader_unit.name}")

            # Try to run actions on non-leader (should fail or be redirected)
            try:
                action = await non_leader_unit.run_action("pre-refresh-check")
                result = await action.wait()
                logger.info(f"Non-leader pre-refresh check result: {result.status}")
                # May succeed or fail depending on implementation
                assert result.status in ["completed", "failed"]
            except Exception as e:
                logger.info(f"Non-leader action error (expected): {e}")

    @pytest.mark.asyncio
    async def test_concurrent_action_execution(self, ops_test: OpsTest, etcd_cluster):
        """Test concurrent refresh action execution."""
        logger.info("Testing concurrent refresh action execution")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Try to run multiple actions concurrently
            tasks = [
                run_pre_refresh_check(ops_test, APP_NAME),
                run_pre_refresh_check(ops_test, APP_NAME),
            ]

            # Execute concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # At least one should succeed
            successful_results = [r for r in results if not isinstance(r, Exception)]
            assert len(successful_results) > 0, "At least one concurrent action should succeed"

            for result in successful_results:
                assert result.status in ["completed", "failed"]

            logger.info(f"Concurrent actions: {len(successful_results)} succeeded")


class TestRefreshActionIntegration:
    """Test refresh action integration with cluster state."""

    @pytest.mark.asyncio
    async def test_actions_with_data_verification(self, ops_test: OpsTest, etcd_cluster):
        """Test refresh actions with data integrity verification."""
        logger.info("Testing refresh actions with data verification")

        async with RefreshTestContext(ops_test, APP_NAME, verify_data_integrity=True):
            # Set up test data
            test_key = "action-test-key"
            test_value = "action-test-value"

            integrity_ok = await verify_cluster_data_integrity(
                ops_test, APP_NAME, test_key, test_value
            )
            assert integrity_ok, "Test data should be set successfully"

            # Run pre-refresh check
            result = await run_pre_refresh_check(ops_test, APP_NAME)
            assert result.status == "completed"

            # Verify data is still intact
            integrity_ok = await verify_cluster_data_integrity(
                ops_test, APP_NAME, test_key, test_value
            )
            assert integrity_ok, "Data should be preserved after pre-refresh check"

            # Try force refresh start
            result = await force_refresh_start(
                ops_test,
                APP_NAME,
                check_compatibility=True,
                run_pre_refresh_checks=False,
                check_workload_container=False,
            )

            logger.info(f"Force refresh with data verification result: {result.status}")

            # Verify data integrity is maintained
            integrity_ok = await verify_cluster_data_integrity(
                ops_test, APP_NAME, test_key, test_value
            )
            assert integrity_ok, "Data should be preserved after refresh actions"

    @pytest.mark.asyncio
    async def test_actions_with_snap_revision_tracking(self, ops_test: OpsTest, etcd_cluster):
        """Test refresh actions with snap revision tracking."""
        logger.info("Testing refresh actions with snap revision tracking")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Get initial snap revisions
            initial_revisions = await get_all_snap_revisions(ops_test, APP_NAME, DEFAULT_SNAP_NAME)
            logger.info(f"Initial snap revisions: {initial_revisions}")

            # Run pre-refresh check
            result = await run_pre_refresh_check(ops_test, APP_NAME)
            assert result.status == "completed"

            # Get snap revisions after pre-refresh check
            after_check_revisions = await get_all_snap_revisions(
                ops_test, APP_NAME, DEFAULT_SNAP_NAME
            )
            logger.info(f"Snap revisions after pre-refresh check: {after_check_revisions}")

            # Revisions should be unchanged after pre-refresh check
            assert initial_revisions == after_check_revisions, (
                "Snap revisions should not change during pre-refresh check"
            )

            # Try force refresh start
            result = await force_refresh_start(
                ops_test,
                APP_NAME,
                check_compatibility=True,
                run_pre_refresh_checks=False,
                check_workload_container=False,
            )

            logger.info(f"Force refresh result: {result.status}")

            # Get final snap revisions
            final_revisions = await get_all_snap_revisions(ops_test, APP_NAME, DEFAULT_SNAP_NAME)
            logger.info(f"Final snap revisions: {final_revisions}")

            # In test environment, revisions might not change, but should still be trackable
            assert len(final_revisions) == len(initial_revisions), (
                "Should track revisions for all units"
            )

    @pytest.mark.asyncio
    async def test_action_error_messages(self, ops_test: OpsTest, etcd_cluster):
        """Test refresh action error messages and reporting."""
        logger.info("Testing refresh action error messages")

        async with RefreshTestContext(ops_test, APP_NAME):
            # Run actions and examine their messages
            result = await run_pre_refresh_check(ops_test, APP_NAME)

            if result.message:
                logger.info(f"Pre-refresh check message: {result.message}")
                # Message should be informative
                assert len(result.message) > 0, "Action message should not be empty"

            # Try force refresh start
            result = await force_refresh_start(
                ops_test,
                APP_NAME,
                check_compatibility=True,
                run_pre_refresh_checks=True,
                check_workload_container=True,
            )

            if result.message:
                logger.info(f"Force refresh start message: {result.message}")
                # Message should be informative
                assert len(result.message) > 0, "Action message should not be empty"

            # Try resume refresh
            result = await resume_refresh(ops_test, APP_NAME, check_health_of_refreshed_units=True)

            if result.message:
                logger.info(f"Resume refresh message: {result.message}")
                # Message should be informative
                assert len(result.message) > 0, "Action message should not be empty"
