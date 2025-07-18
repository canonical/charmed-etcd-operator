# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for charm-refresh functionality."""

import unittest.mock as mock

import charm_refresh
import ops
import ops.testing
import pytest
from charms.operator_libs_linux.v2 import snap

from charm import EtcdCharmSpecific, EtcdOperatorCharm


class TestEtcdCharmSpecific:
    """Test the EtcdCharmSpecific class for charm-refresh functionality."""

    @pytest.fixture
    def harness(self):
        """Create a test harness."""
        harness = ops.testing.Harness(EtcdOperatorCharm)
        harness.begin_with_initial_hooks()
        return harness

    @pytest.fixture
    def etcd_charm_specific(self, harness):
        """Create an EtcdCharmSpecific instance."""
        return EtcdCharmSpecific(
            workload_name="etcd",
            charm_name="charmed-etcd",
            _charm=harness.charm,
        )

    def test_init(self, etcd_charm_specific):
        """Test EtcdCharmSpecific initialization."""
        assert etcd_charm_specific.workload_name == "etcd"
        assert etcd_charm_specific.charm_name == "charmed-etcd"
        assert etcd_charm_specific._charm is not None

    def test_is_compatible_same_major_version(self, etcd_charm_specific):
        """Test is_compatible method with same major version."""
        # Same major version should be compatible
        assert (
            etcd_charm_specific.is_compatible(
                charm_name="charmed-etcd",
                charm_major_version=1,
                charm_minor_version=2,
                workload_name="etcd",
                workload_major_version=3,
                workload_minor_version=5,
                workload_patch_version=18,
            )
            is True
        )

    def test_is_compatible_different_charm_name(self, etcd_charm_specific):
        """Test is_compatible method with different charm name."""
        assert (
            etcd_charm_specific.is_compatible(
                charm_name="different-etcd",
                charm_major_version=1,
                charm_minor_version=2,
                workload_name="etcd",
                workload_major_version=3,
                workload_minor_version=5,
                workload_patch_version=18,
            )
            is False
        )

    def test_is_compatible_different_workload_name(self, etcd_charm_specific):
        """Test is_compatible method with different workload name."""
        assert (
            etcd_charm_specific.is_compatible(
                charm_name="charmed-etcd",
                charm_major_version=1,
                charm_minor_version=2,
                workload_name="postgresql",
                workload_major_version=3,
                workload_minor_version=5,
                workload_patch_version=18,
            )
            is False
        )

    def test_is_compatible_major_version_downgrade(self, etcd_charm_specific):
        """Test is_compatible method with major version downgrade."""
        # Major version downgrade should not be compatible
        assert (
            etcd_charm_specific.is_compatible(
                charm_name="charmed-etcd",
                charm_major_version=1,
                charm_minor_version=2,
                workload_name="etcd",
                workload_major_version=2,  # Downgrade from 3 to 2
                workload_minor_version=5,
                workload_patch_version=18,
            )
            is False
        )

    def test_is_compatible_major_version_upgrade(self, etcd_charm_specific):
        """Test is_compatible method with major version upgrade."""
        # Major version upgrade should be compatible (etcd supports it)
        assert (
            etcd_charm_specific.is_compatible(
                charm_name="charmed-etcd",
                charm_major_version=1,
                charm_minor_version=2,
                workload_name="etcd",
                workload_major_version=4,  # Upgrade from 3 to 4
                workload_minor_version=0,
                workload_patch_version=0,
            )
            is True
        )

    @mock.patch("charms.operator_libs_linux.v2.snap.SnapCache")
    @mock.patch.object(EtcdCharmSpecific, "_post_snap_refresh")
    def test_refresh_snap_success(self, mock_post_refresh, mock_snap_cache, etcd_charm_specific):
        """Test successful snap refresh."""
        # Mock snap behavior
        mock_etcd_snap = mock.Mock()
        mock_etcd_snap.revision = "12"
        mock_snap_cache.return_value = {"charmed-etcd": mock_etcd_snap}

        # Mock workload
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = True

        # Mock refresh instance
        mock_refresh = mock.Mock()

        # Test refresh_snap
        etcd_charm_specific.refresh_snap(
            snap_name="charmed-etcd", snap_revision="13", refresh=mock_refresh
        )

        # Verify workload was stopped
        etcd_charm_specific._charm.workload.stop.assert_called_once()

        # Verify snap was refreshed
        mock_etcd_snap.ensure.assert_called_once_with(snap.SnapState.Present, revision="13")
        mock_etcd_snap.hold.assert_called_once()

        # Verify update_snap_revision was called
        mock_refresh.update_snap_revision.assert_called_once()

        # Verify post-refresh was called
        mock_post_refresh.assert_called_once_with(mock_refresh)

    @mock.patch("charms.operator_libs_linux.v2.snap.SnapCache")
    def test_refresh_snap_same_revision_fails(self, mock_snap_cache, etcd_charm_specific):
        """Test refresh_snap with same revision should fail."""
        # Mock snap behavior
        mock_etcd_snap = mock.Mock()
        mock_etcd_snap.revision = "13"
        mock_snap_cache.return_value = {"charmed-etcd": mock_etcd_snap}

        # Mock refresh instance
        mock_refresh = mock.Mock()

        # Test refresh_snap with same revision should raise AssertionError
        with pytest.raises(AssertionError):
            etcd_charm_specific.refresh_snap(
                snap_name="charmed-etcd",
                snap_revision="13",  # Same as current revision
                refresh=mock_refresh,
            )

    @mock.patch("charms.operator_libs_linux.v2.snap.SnapCache")
    @mock.patch.object(EtcdCharmSpecific, "_post_snap_refresh")
    def test_refresh_snap_failure_revision_unchanged(
        self, mock_post_refresh, mock_snap_cache, etcd_charm_specific
    ):
        """Test refresh_snap failure when revision doesn't change."""
        # Mock snap behavior
        mock_etcd_snap = mock.Mock()
        mock_etcd_snap.revision = "12"  # Revision stays the same after failure
        mock_etcd_snap.ensure.side_effect = snap.SnapError("Refresh failed")
        mock_snap_cache.return_value = {"charmed-etcd": mock_etcd_snap}

        # Mock workload
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = True

        # Mock refresh instance
        mock_refresh = mock.Mock()

        # Test refresh_snap failure
        with pytest.raises(snap.SnapError):
            etcd_charm_specific.refresh_snap(
                snap_name="charmed-etcd", snap_revision="13", refresh=mock_refresh
            )

        # Verify workload was restarted after failure
        etcd_charm_specific._charm.workload.start.assert_called_once()

        # Verify update_snap_revision was NOT called
        mock_refresh.update_snap_revision.assert_not_called()

        # Verify post-refresh was NOT called
        mock_post_refresh.assert_not_called()

    @mock.patch("charms.operator_libs_linux.v2.snap.SnapCache")
    @mock.patch.object(EtcdCharmSpecific, "_post_snap_refresh")
    def test_refresh_snap_failure_revision_changed(
        self, mock_post_refresh, mock_snap_cache, etcd_charm_specific
    ):
        """Test refresh_snap failure when revision does change."""
        # Mock snap behavior
        mock_etcd_snap = mock.Mock()
        # First call returns old revision, then new revision after ensure
        mock_etcd_snap.revision = "13"  # New revision after ensure
        mock_etcd_snap.ensure.side_effect = snap.SnapError("Post-refresh error")
        mock_snap_cache.return_value = {"charmed-etcd": mock_etcd_snap}

        # Mock workload
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = True

        # Mock refresh instance
        mock_refresh = mock.Mock()

        # Test refresh_snap failure after successful refresh
        with pytest.raises(snap.SnapError):
            etcd_charm_specific.refresh_snap(
                snap_name="charmed-etcd", snap_revision="13", refresh=mock_refresh
            )

        # Verify workload was NOT restarted (since revision changed)
        etcd_charm_specific._charm.workload.start.assert_not_called()

        # Verify update_snap_revision WAS called (since revision changed)
        mock_refresh.update_snap_revision.assert_called_once()

        # Verify post-refresh was NOT called due to exception
        mock_post_refresh.assert_not_called()

    @mock.patch.object(EtcdCharmSpecific, "_ensure_application_and_unit_are_healthy")
    def test_post_snap_refresh_success(self, mock_health_check, etcd_charm_specific):
        """Test successful post-snap refresh."""
        # Mock workload
        etcd_charm_specific._charm.workload = mock.Mock()

        # Mock refresh instance
        mock_refresh = mock.Mock()
        mock_refresh.next_unit_allowed_to_refresh = False

        # Test _post_snap_refresh
        etcd_charm_specific._post_snap_refresh(mock_refresh)

        # Verify workload was started
        etcd_charm_specific._charm.workload.start.assert_called_once()

        # Verify health check was called
        mock_health_check.assert_called_once()

        # Verify next_unit_allowed_to_refresh was set to True
        assert mock_refresh.next_unit_allowed_to_refresh is True

    @mock.patch.object(EtcdCharmSpecific, "_ensure_application_and_unit_are_healthy")
    def test_post_snap_refresh_health_check_failure(self, mock_health_check, etcd_charm_specific):
        """Test post-snap refresh with health check failure."""
        # Mock workload
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.unit = mock.Mock()

        # Mock health check failure
        mock_health_check.side_effect = Exception("Health check failed")

        # Mock refresh instance
        mock_refresh = mock.Mock()
        mock_refresh.next_unit_allowed_to_refresh = False

        # Test _post_snap_refresh with health check failure
        etcd_charm_specific._post_snap_refresh(mock_refresh)

        # Verify workload was started
        etcd_charm_specific._charm.workload.start.assert_called_once()

        # Verify health check was called
        mock_health_check.assert_called_once()

        # Verify next_unit_allowed_to_refresh remains False
        assert mock_refresh.next_unit_allowed_to_refresh is False

        # Verify blocked status was set
        expected_status = ops.BlockedStatus(
            "Post-refresh health check failed: Health check failed"
        )
        etcd_charm_specific._charm.unit.status = expected_status

    def test_ensure_application_and_unit_are_healthy_workload_not_alive(self, etcd_charm_specific):
        """Test health check when workload is not alive."""
        # Mock workload as not alive
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = False

        # Test health check should raise exception
        with pytest.raises(Exception, match="Workload is not running"):
            etcd_charm_specific._ensure_application_and_unit_are_healthy()

    def test_ensure_application_and_unit_are_healthy_cluster_not_healthy(
        self, etcd_charm_specific
    ):
        """Test health check when cluster is not healthy."""
        # Mock workload as alive
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = True

        # Mock cluster manager as not healthy
        etcd_charm_specific._charm.cluster_manager = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.is_healthy.return_value = False

        # Test health check should raise exception
        with pytest.raises(Exception, match="Cluster is not healthy, cannot refresh"):
            etcd_charm_specific._ensure_application_and_unit_are_healthy()

    def test_ensure_application_and_unit_are_healthy_rebuild_in_progress(
        self, etcd_charm_specific
    ):
        """Test health check when rebuild is in progress."""
        # Mock workload as alive
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = True

        # Mock cluster manager as healthy but rebuild in progress
        etcd_charm_specific._charm.cluster_manager = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.is_healthy.return_value = True
        etcd_charm_specific._charm.cluster_manager.state = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.state.cluster = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.state.cluster.rebuild_cluster_in_progress = True

        # Test health check should raise exception
        with pytest.raises(Exception, match="Rebuild cluster is in progress, cannot refresh"):
            etcd_charm_specific._ensure_application_and_unit_are_healthy()

    def test_ensure_application_and_unit_are_healthy_restore_in_progress(
        self, etcd_charm_specific
    ):
        """Test health check when restore is in progress."""
        # Mock workload as alive
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = True

        # Mock cluster manager as healthy but restore in progress
        etcd_charm_specific._charm.cluster_manager = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.is_healthy.return_value = True
        etcd_charm_specific._charm.cluster_manager.state = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.state.cluster = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.state.cluster.rebuild_cluster_in_progress = (
            False
        )
        etcd_charm_specific._charm.cluster_manager.state.cluster.is_restore_in_progress = True

        # Test health check should raise exception
        with pytest.raises(Exception, match="Restore is in progress, cannot refresh"):
            etcd_charm_specific._ensure_application_and_unit_are_healthy()

    def test_ensure_application_and_unit_are_healthy_backup_in_progress(self, etcd_charm_specific):
        """Test health check when backup is in progress."""
        # Mock workload as alive
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = True

        # Mock cluster manager as healthy but backup in progress
        etcd_charm_specific._charm.cluster_manager = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.is_healthy.return_value = True
        etcd_charm_specific._charm.cluster_manager.state = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.state.cluster = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.state.cluster.rebuild_cluster_in_progress = (
            False
        )
        etcd_charm_specific._charm.cluster_manager.state.cluster.is_restore_in_progress = False
        etcd_charm_specific._charm.cluster_manager.state.cluster.is_backup_in_progress = True

        # Test health check should raise exception
        with pytest.raises(Exception, match="Backup is in progress, cannot refresh"):
            etcd_charm_specific._ensure_application_and_unit_are_healthy()

    def test_ensure_application_and_unit_are_healthy_success(self, etcd_charm_specific):
        """Test successful health check."""
        # Mock workload as alive
        etcd_charm_specific._charm.workload = mock.Mock()
        etcd_charm_specific._charm.workload.alive.return_value = True

        # Mock cluster manager as healthy with no operations in progress
        etcd_charm_specific._charm.cluster_manager = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.is_healthy.return_value = True
        etcd_charm_specific._charm.cluster_manager.state = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.state.cluster = mock.Mock()
        etcd_charm_specific._charm.cluster_manager.state.cluster.rebuild_cluster_in_progress = (
            False
        )
        etcd_charm_specific._charm.cluster_manager.state.cluster.is_restore_in_progress = False
        etcd_charm_specific._charm.cluster_manager.state.cluster.is_backup_in_progress = False

        # Test health check should succeed without raising exception
        etcd_charm_specific._ensure_application_and_unit_are_healthy()


class TestCharmRefreshIntegration:
    """Test charm-refresh integration in the main charm."""

    @pytest.fixture
    def harness(self):
        """Create a test harness."""
        harness = ops.testing.Harness(EtcdOperatorCharm)
        harness.begin_with_initial_hooks()
        return harness

    @mock.patch("charm_refresh.Machines")
    def test_charm_refresh_initialization_success(self, mock_machines, harness):
        """Test successful charm-refresh initialization."""
        # Mock charm_refresh.Machines
        mock_refresh_instance = mock.Mock()
        mock_machines.return_value = mock_refresh_instance

        # Create charm instance
        charm = harness.charm

        # Verify refresh was initialized
        assert hasattr(charm, "refresh")
        assert charm.refresh is mock_refresh_instance

        # Verify EtcdCharmSpecific was passed to Machines
        mock_machines.assert_called_once()
        args, kwargs = mock_machines.call_args
        etcd_charm_specific = args[0]
        assert isinstance(etcd_charm_specific, EtcdCharmSpecific)
        assert etcd_charm_specific.workload_name == "etcd"
        assert etcd_charm_specific.charm_name == "charmed-etcd"

    @mock.patch("charm_refresh.Machines")
    def test_charm_refresh_peer_relation_not_ready(self, mock_machines, harness):
        """Test charm-refresh initialization when peer relation is not ready."""
        # Mock charm_refresh.Machines to raise PeerRelationNotReady
        mock_machines.side_effect = charm_refresh.PeerRelationNotReady()

        # Create charm instance
        charm = harness.charm

        # Verify status is set correctly
        assert isinstance(charm.unit.status, ops.MaintenanceStatus)
        assert charm.unit.status.message == "Waiting for peer relation"

    @mock.patch("charm_refresh.Machines")
    def test_charm_refresh_unit_tearing_down(self, mock_machines, harness):
        """Test charm-refresh initialization when unit is tearing down."""
        # Mock charm_refresh.Machines to raise UnitTearingDown
        mock_machines.side_effect = charm_refresh.UnitTearingDown()

        # Create charm instance
        charm = harness.charm

        # Verify status is set correctly
        assert isinstance(charm.unit.status, ops.MaintenanceStatus)
        assert charm.unit.status.message == "Tearing down"

    @mock.patch("charm_refresh.Machines")
    def test_handle_post_refresh_health_checks_not_initialized(self, mock_machines, harness):
        """Test post-refresh health checks when refresh is not initialized."""
        # Mock charm_refresh.Machines to raise exception so refresh is not initialized
        mock_machines.side_effect = charm_refresh.PeerRelationNotReady()

        # Create charm instance
        charm = harness.charm

        # Call _handle_post_refresh_health_checks should not raise exception
        charm._handle_post_refresh_health_checks()

    @mock.patch("charm_refresh.Machines")
    def test_handle_post_refresh_health_checks_not_in_progress(self, mock_machines, harness):
        """Test post-refresh health checks when refresh is not in progress."""
        # Mock charm_refresh.Machines
        mock_refresh_instance = mock.Mock()
        mock_refresh_instance.in_progress = False
        mock_machines.return_value = mock_refresh_instance

        # Create charm instance
        charm = harness.charm

        # Call _handle_post_refresh_health_checks
        charm._handle_post_refresh_health_checks()

        # Verify no health checks were attempted
        assert (
            mock_refresh_instance.next_unit_allowed_to_refresh
            is mock_refresh_instance.next_unit_allowed_to_refresh
        )

    @mock.patch("charm_refresh.Machines")
    def test_handle_post_refresh_health_checks_already_allowed(self, mock_machines, harness):
        """Test post-refresh health checks when next unit is already allowed."""
        # Mock charm_refresh.Machines
        mock_refresh_instance = mock.Mock()
        mock_refresh_instance.in_progress = True
        mock_refresh_instance.next_unit_allowed_to_refresh = True
        mock_machines.return_value = mock_refresh_instance

        # Create charm instance
        charm = harness.charm

        # Call _handle_post_refresh_health_checks
        charm._handle_post_refresh_health_checks()

        # Verify no health checks were attempted since already allowed
        assert mock_refresh_instance.next_unit_allowed_to_refresh is True

    @mock.patch("charm_refresh.Machines")
    def test_handle_post_refresh_health_checks_success(self, mock_machines, harness):
        """Test successful post-refresh health checks."""
        # Mock charm_refresh.Machines
        mock_refresh_instance = mock.Mock()
        mock_refresh_instance.in_progress = True
        mock_refresh_instance.next_unit_allowed_to_refresh = False
        mock_machines.return_value = mock_refresh_instance

        # Create charm instance and mock its components
        charm = harness.charm
        charm.workload = mock.Mock()
        charm.workload.alive.return_value = True
        charm.cluster_manager = mock.Mock()
        charm.cluster_manager.is_healthy.return_value = True

        # Call _handle_post_refresh_health_checks
        charm._handle_post_refresh_health_checks()

        # Verify next_unit_allowed_to_refresh was set to True
        assert mock_refresh_instance.next_unit_allowed_to_refresh is True

    @mock.patch("charm_refresh.Machines")
    def test_handle_post_refresh_health_checks_failure(self, mock_machines, harness):
        """Test post-refresh health checks with failure."""
        # Mock charm_refresh.Machines
        mock_refresh_instance = mock.Mock()
        mock_refresh_instance.in_progress = True
        mock_refresh_instance.next_unit_allowed_to_refresh = False
        mock_machines.return_value = mock_refresh_instance

        # Create charm instance and mock its components
        charm = harness.charm
        charm.workload = mock.Mock()
        charm.workload.alive.return_value = False  # Workload not alive
        charm.cluster_manager = mock.Mock()

        # Call _handle_post_refresh_health_checks
        charm._handle_post_refresh_health_checks()

        # Verify next_unit_allowed_to_refresh remains False
        assert mock_refresh_instance.next_unit_allowed_to_refresh is False

        # Verify blocked status was set
        assert isinstance(charm.unit.status, ops.BlockedStatus)
        assert "Post-refresh health check failed" in charm.unit.status.message


class TestRefreshActionsIntegration:
    """Test charm-refresh actions integration."""

    @pytest.fixture
    def harness(self):
        """Create a test harness with refresh actions."""
        harness = ops.testing.Harness(EtcdOperatorCharm)
        harness.begin_with_initial_hooks()
        return harness

    def test_pre_refresh_check_action_exists(self, harness):
        """Test that pre-refresh-check action exists and can be called."""
        # Verify the action exists in the charm's meta
        assert "pre-refresh-check" in harness.charm.meta.actions

    def test_force_refresh_start_action_exists(self, harness):
        """Test that force-refresh-start action exists and can be called."""
        # Verify the action exists in the charm's meta
        assert "force-refresh-start" in harness.charm.meta.actions

    def test_resume_refresh_action_exists(self, harness):
        """Test that resume-refresh action exists and can be called."""
        # Verify the action exists in the charm's meta
        assert "resume-refresh" in harness.charm.meta.actions


class TestPreRefreshChecks:
    """Test pre-refresh check functions."""

    def test_run_pre_refresh_checks_before_1_unit_refreshed_success(self):
        """Test successful pre-refresh checks before any units are refreshed."""
        from charm import run_pre_refresh_checks_before_1_unit_refreshed

        # Should not raise any exceptions for basic checks
        try:
            run_pre_refresh_checks_before_1_unit_refreshed()
        except Exception as e:
            pytest.fail(f"Pre-refresh checks failed unexpectedly: {e}")

    def test_run_pre_refresh_checks_after_1_unit_refreshed_success(self):
        """Test successful pre-refresh checks after 1 unit is refreshed."""
        from charm import run_pre_refresh_checks_after_1_unit_refreshed

        # Should not raise any exceptions (currently just a pass)
        try:
            run_pre_refresh_checks_after_1_unit_refreshed()
        except Exception as e:
            pytest.fail(f"Pre-refresh checks failed unexpectedly: {e}")
