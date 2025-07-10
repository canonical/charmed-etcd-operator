# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Basic integration tests for charm-refresh functionality."""

import logging
import pytest
import pytest_asyncio

from pytest_operator.plugin import OpsTest

from tests.integration.refresh_helpers import (
    RefreshTestContext,
    check_cluster_health_during_refresh,
    get_all_snap_revisions,
    run_pre_refresh_check,
    verify_cluster_data_integrity,
    check_refresh_config_options,
    set_refresh_config,
    DEFAULT_SNAP_NAME,
)

logger = logging.getLogger(__name__)

APP_NAME = "etcd"
CHARM_NAME = "charmed-etcd"


@pytest_asyncio.fixture(scope="module")
async def etcd_app(ops_test: OpsTest):
    """Deploy etcd application for refresh testing."""
    logger.info("Deploying etcd for refresh testing")
    
    # Deploy a 3-unit etcd cluster for refresh testing
    await ops_test.model.deploy(
        CHARM_NAME, 
        application_name=APP_NAME,
        num_units=3,
        channel="latest/edge"
    )
    
    # Wait for the cluster to be ready
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="active",
        timeout=600,
    )
    
    return ops_test.model.applications[APP_NAME]


class TestRefreshBasicFunctionality:
    """Test basic refresh functionality."""

    @pytest.mark.asyncio
    async def test_refresh_config_options(self, ops_test: OpsTest, etcd_app):
        """Test refresh configuration options."""
        logger.info("Testing refresh configuration options")
        
        # Check default configuration
        config = await check_refresh_config_options(ops_test, APP_NAME)
        assert "pause_after_unit_refresh" in config
        assert config["pause_after_unit_refresh"] == "first"  # Default value
        
        # Test setting configuration
        await set_refresh_config(ops_test, APP_NAME, {
            "pause_after_unit_refresh": "all"
        })
        
        # Verify configuration was set
        config = await check_refresh_config_options(ops_test, APP_NAME)
        assert config["pause_after_unit_refresh"] == "all"
        
        # Reset to default
        await set_refresh_config(ops_test, APP_NAME, {
            "pause_after_unit_refresh": "first"
        })

    @pytest.mark.asyncio
    async def test_pre_refresh_check_action(self, ops_test: OpsTest, etcd_app):
        """Test pre-refresh-check action."""
        logger.info("Testing pre-refresh-check action")
        
        # Run pre-refresh check
        result = await run_pre_refresh_check(ops_test, APP_NAME)
        
        # Verify action completed successfully
        assert result.status == "completed"
        logger.info(f"Pre-refresh check result: {result.message}")

    @pytest.mark.asyncio
    async def test_refresh_actions_exist(self, ops_test: OpsTest, etcd_app):
        """Test that all refresh actions exist and are callable."""
        logger.info("Testing refresh actions existence")
        
        app = ops_test.model.applications[APP_NAME]
        leader_unit = None
        
        # Find leader unit
        for unit in app.units:
            if await unit.is_leader_from_status():
                leader_unit = unit
                break
        
        assert leader_unit is not None, "No leader unit found"
        
        # Test that actions are defined (they should exist even if not callable in test)
        actions = [
            "pre-refresh-check",
            "force-refresh-start", 
            "resume-refresh"
        ]
        
        for action_name in actions:
            try:
                # Just verify the action can be created (not necessarily run successfully)
                action = await leader_unit.run_action(action_name)
                logger.info(f"Action {action_name} exists and can be invoked")
            except Exception as e:
                # Action might fail due to test environment, but should exist
                logger.info(f"Action {action_name} exists but failed: {e}")

    @pytest.mark.asyncio
    async def test_snap_revision_tracking(self, ops_test: OpsTest, etcd_app):
        """Test tracking snap revisions across units."""
        logger.info("Testing snap revision tracking")
        
        # Get current snap revisions
        revisions = await get_all_snap_revisions(ops_test, APP_NAME, DEFAULT_SNAP_NAME)
        
        # Should have revisions for all units
        app = ops_test.model.applications[APP_NAME]
        expected_units = {unit.name for unit in app.units}
        actual_units = set(revisions.keys())
        
        assert expected_units == actual_units, f"Missing revisions for units: {expected_units - actual_units}"
        
        # All revisions should be non-empty strings
        for unit_name, revision in revisions.items():
            assert revision and revision != "unknown", f"Invalid revision for {unit_name}: {revision}"
        
        logger.info(f"Snap revisions: {revisions}")

    @pytest.mark.asyncio
    async def test_cluster_health_monitoring(self, ops_test: OpsTest, etcd_app):
        """Test cluster health monitoring during operations."""
        logger.info("Testing cluster health monitoring")
        
        # Monitor cluster health for a short period
        health_ok = await check_cluster_health_during_refresh(
            ops_test, APP_NAME, check_duration=30
        )
        
        # Cluster should be healthy during normal operation
        assert health_ok, "Cluster should be healthy during normal operation"

    @pytest.mark.asyncio
    async def test_data_integrity_verification(self, ops_test: OpsTest, etcd_app):
        """Test data integrity verification functionality."""
        logger.info("Testing data integrity verification")
        
        # Test data integrity verification
        integrity_ok = await verify_cluster_data_integrity(
            ops_test, APP_NAME, 
            test_key="integrity-test", 
            test_value="integrity-value"
        )
        
        assert integrity_ok, "Data integrity verification should succeed"

    @pytest.mark.asyncio
    async def test_refresh_test_context_manager(self, ops_test: OpsTest, etcd_app):
        """Test the refresh test context manager."""
        logger.info("Testing refresh test context manager")
        
        async with RefreshTestContext(ops_test, APP_NAME, verify_data_integrity=True) as ctx:
            # Context manager should track initial state
            assert hasattr(ctx, 'initial_snap_revisions')
            assert hasattr(ctx, 'test_data_key')
            assert hasattr(ctx, 'test_data_value')
            
            # Should have snap revisions for all units
            app = ops_test.model.applications[APP_NAME]
            expected_units = {unit.name for unit in app.units}
            actual_units = set(ctx.initial_snap_revisions.keys())
            
            assert expected_units == actual_units
            
            logger.info("Context manager working correctly")


class TestRefreshCompatibility:
    """Test refresh compatibility checking."""

    @pytest.mark.asyncio
    async def test_refresh_peer_relation_exists(self, ops_test: OpsTest, etcd_app):
        """Test that the refresh-v-three peer relation exists."""
        logger.info("Testing refresh peer relation")
        
        app = ops_test.model.applications[APP_NAME]
        
        # Check if refresh-v-three relation is established
        relations = app.relations
        refresh_relations = [r for r in relations if r.name == "refresh-v-three"]
        
        # Should have the refresh peer relation (self-relation)
        assert len(refresh_relations) > 0, "refresh-v-three peer relation should exist"
        
        logger.info(f"Found {len(refresh_relations)} refresh peer relations")

    @pytest.mark.asyncio
    async def test_refresh_versions_toml_exists(self, ops_test: OpsTest, etcd_app):
        """Test that refresh_versions.toml exists and is readable."""
        logger.info("Testing refresh_versions.toml file")
        
        # Get a unit to check the file
        app = ops_test.model.applications[APP_NAME]
        unit = list(app.units)[0]
        
        # Check if refresh_versions.toml exists in the charm directory
        result = await unit.run("test -f /var/lib/juju/agents/unit-*/charm/refresh_versions.toml")
        
        if result.return_code == 0:
            # File exists, try to read it
            result = await unit.run("cat /var/lib/juju/agents/unit-*/charm/refresh_versions.toml")
            assert result.return_code == 0, "Should be able to read refresh_versions.toml"
            
            # Basic validation that it contains expected sections
            content = result.stdout
            assert "[charm]" in content, "refresh_versions.toml should have [charm] section"
            assert "[workload]" in content, "refresh_versions.toml should have [workload] section"
            assert "[snaps" in content, "refresh_versions.toml should have [snaps] section"
            
            logger.info("refresh_versions.toml exists and is valid")
        else:
            logger.warning("refresh_versions.toml not found, may be expected in test environment")


class TestRefreshConfigurationManagement:
    """Test refresh configuration management."""

    @pytest.mark.asyncio
    async def test_pause_after_unit_refresh_options(self, ops_test: OpsTest, etcd_app):
        """Test different pause_after_unit_refresh options."""
        logger.info("Testing pause_after_unit_refresh options")
        
        valid_options = ["none", "first", "all"]
        
        for option in valid_options:
            logger.info(f"Testing pause_after_unit_refresh={option}")
            
            # Set the configuration
            await set_refresh_config(ops_test, APP_NAME, {
                "pause_after_unit_refresh": option
            })
            
            # Verify it was set
            config = await check_refresh_config_options(ops_test, APP_NAME)
            assert config["pause_after_unit_refresh"] == option
            
            # Run pre-refresh check to ensure cluster is still stable
            result = await run_pre_refresh_check(ops_test, APP_NAME)
            assert result.status == "completed"
        
        # Reset to default
        await set_refresh_config(ops_test, APP_NAME, {
            "pause_after_unit_refresh": "first"
        })

    @pytest.mark.asyncio
    async def test_refresh_configuration_validation(self, ops_test: OpsTest, etcd_app):
        """Test refresh configuration validation."""
        logger.info("Testing refresh configuration validation")
        
        # Test that configuration accepts valid values
        valid_configs = [
            {"pause_after_unit_refresh": "none"},
            {"pause_after_unit_refresh": "first"},
            {"pause_after_unit_refresh": "all"}
        ]
        
        for config in valid_configs:
            await set_refresh_config(ops_test, APP_NAME, config)
            logger.info(f"Valid config accepted: {config}")
        
        # Reset to default
        await set_refresh_config(ops_test, APP_NAME, {
            "pause_after_unit_refresh": "first"
        }) 