#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, patch

import pytest
from charm_refresh import PrecheckFailed
from ops import BlockedStatus, testing

from charm import EtcdOperatorCharm
from common.exceptions import EtcdUpgradeError
from literals import PEER_RELATION
from src.events.refresh import MachinesEtcdRefresh


@pytest.mark.parametrize(
    "old_version, new_version, expected",
    [
        ("3.6.0", "3.6.1", True),  # Patch upgrade allowed
        ("3.6.0", "3.7.0", False),  # Minor upgrade not allowed
        ("3.6.0", "4.0.0", False),  # Major upgrade not allowed
        ("3.6.0", "3.5.0", False),  # Downgrade not allowed
        ("3.6.1", "3.6.0", False),  # Downgrade not allowed
        ("invalid", "3.6.0", False),  # Invalid version format
        ("3.6.0", "invalid", False),  # Invalid version format
    ],
)
def test_is_workload_compatible(old_version: str, new_version: str, expected: bool) -> None:
    assert MachinesEtcdRefresh.is_workload_compatible(old_version, new_version) == expected


@pytest.mark.parametrize(
    "app_data, unit_data, pre_check_result",
    [
        ({"backup_id": "XYZ"}, {}, "Backup in progress"),
        ({"restore_id": "XYZ"}, {}, "Restore in progress"),
        ({"rebuild_cluster": "True"}, {}, "Cluster rebuild in progress"),
        ({}, {"tls_client_state": "to-tls"}, "TLS transition is in progress"),
        ({}, {"tls_peer_ca_rotation": "new-ca-detected"}, "TLS CA rotation is in progress"),
    ],
)
def test_pre_refresh_checks(app_data, unit_data, pre_check_result) -> None:
    ctx = testing.Context(EtcdOperatorCharm)

    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data=app_data,
        local_unit_data=unit_data,
    )

    state_in = testing.State(relations={peer_relation})

    with ctx(ctx.on.relation_changed(relation=peer_relation), state_in) as manager:
        charm: EtcdOperatorCharm = manager.charm

        # Mock the refresh constructor to avoid version checks
        with patch("events.refresh.MachinesEtcdRefresh.__init__", return_value=None):
            refresh = MachinesEtcdRefresh.__new__(MachinesEtcdRefresh)
            refresh.charm = charm
            with pytest.raises(PrecheckFailed) as e:
                refresh.run_pre_refresh_checks_after_1_unit_refreshed()

            assert str(e.value) == pre_check_result


def test_pre_refresh_checks_unhealthy_cluster() -> None:
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    state_in = testing.State(relations={peer_relation})

    with patch("managers.cluster.ClusterManager.is_healthy", return_value=False):
        with ctx(ctx.on.relation_changed(relation=peer_relation), state_in) as manager:
            charm: EtcdOperatorCharm = manager.charm

            # Mock the refresh constructor to avoid version checks
            with patch("events.refresh.MachinesEtcdRefresh.__init__", return_value=None):
                refresh = MachinesEtcdRefresh.__new__(MachinesEtcdRefresh)
                refresh.charm = charm
                with pytest.raises(PrecheckFailed) as e:
                    refresh.run_pre_refresh_checks_after_1_unit_refreshed()

                assert str(e.value) == "Cluster is not healthy"


def test_snap_refresh_successful() -> None:
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    state_in = testing.State(relations={peer_relation})

    with (
        patch("managers.cluster.ClusterManager.move_leader_if_required"),
        patch("workload.EtcdWorkload.stop"),
        patch("workload.EtcdWorkload.install", return_value=True),
        patch("workload.EtcdWorkload.snap_revision", return_value="123"),
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.write_file"),
        patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
    ):
        with ctx(ctx.on.relation_changed(relation=peer_relation), state_in) as manager:
            mock_refresh = MagicMock()
            mock_refresh.next_unit_allowed_to_refresh = False
            charm: EtcdOperatorCharm = manager.charm

            # Mock the refresh constructor to avoid version checks
            with patch("events.refresh.MachinesEtcdRefresh.__init__", return_value=None):
                refresh = MachinesEtcdRefresh.__new__(MachinesEtcdRefresh)
                refresh.charm = charm
                refresh.refresh_snap(
                    snap_name="charmed-etcd", snap_revision="124", refresh=mock_refresh
                )

            assert mock_refresh.next_unit_allowed_to_refresh


def test_snap_refresh_failed() -> None:
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    state_in = testing.State(relations={peer_relation})

    with (
        patch("managers.cluster.ClusterManager.move_leader_if_required"),
        patch("workload.EtcdWorkload.stop"),
        patch("workload.EtcdWorkload.install", return_value=False),
        patch("workload.EtcdWorkload.snap_revision", return_value="123"),
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.write_file"),
        patch("managers.cluster.ClusterManager.is_healthy", return_value=False),
    ):
        with ctx(ctx.on.relation_changed(relation=peer_relation), state_in) as manager:
            mock_refresh = MagicMock()
            mock_refresh.next_unit_allowed_to_refresh = False
            charm: EtcdOperatorCharm = manager.charm

            # Mock the refresh constructor to avoid version checks
            with patch("events.refresh.MachinesEtcdRefresh.__init__", return_value=None):
                refresh = MachinesEtcdRefresh.__new__(MachinesEtcdRefresh)
                refresh.charm = charm
                with pytest.raises(EtcdUpgradeError):
                    refresh.refresh_snap(
                        snap_name="charmed-etcd", snap_revision="124", refresh=mock_refresh
                    )

            assert not mock_refresh.next_unit_allowed_to_refresh


def test_post_snap_refresh_healthy_cluster() -> None:
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    state_in = testing.State(relations={peer_relation})

    with (
        patch("workload.EtcdWorkload.restart"),
        patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
    ):
        with ctx(ctx.on.relation_changed(relation=peer_relation), state_in) as manager:
            mock_refresh = MagicMock()
            mock_refresh.next_unit_allowed_to_refresh = False

            charm: EtcdOperatorCharm = manager.charm
            charm.refresh = mock_refresh
            charm._post_snap_refresh()

            assert mock_refresh.next_unit_allowed_to_refresh


def test_post_snap_refresh_unhealthy_cluster() -> None:
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    state_in = testing.State(relations={peer_relation})

    with (
        patch("workload.EtcdWorkload.restart"),
        patch("managers.cluster.ClusterManager.is_healthy", return_value=False),
    ):
        with ctx(ctx.on.relation_changed(relation=peer_relation), state_in) as manager:
            mock_refresh = MagicMock()
            mock_refresh.next_unit_allowed_to_refresh = False

            charm: EtcdOperatorCharm = manager.charm
            charm.refresh = mock_refresh
            charm._post_snap_refresh()

            assert not mock_refresh.next_unit_allowed_to_refresh


def test_statuses() -> None:
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    state_in = testing.State(relations={peer_relation})

    # higher app status
    refresh_mock = MagicMock()
    refresh_mock.app_status_higher_priority = BlockedStatus("123")
    refresh_mock.unit_status_higher_priority = None
    with patch("charm_refresh.Machines", MagicMock(return_value=refresh_mock)):
        state_out = ctx.run(ctx.on.update_status(), state_in)

        assert state_out.unit_status == BlockedStatus("123")

    # higher unit status
    refresh_mock = MagicMock()
    refresh_mock.app_status_higher_priority = None
    refresh_mock.unit_status_higher_priority = BlockedStatus("456")
    with patch("charm_refresh.Machines", MagicMock(return_value=refresh_mock)):
        state_out = ctx.run(ctx.on.update_status(), state_in)

        assert state_out.unit_status == BlockedStatus("456")

    # lower unit status
    refresh_mock = MagicMock()
    refresh_mock.app_status_higher_priority = None
    refresh_mock.unit_status_higher_priority = None
    # refresh_mock.unit_status_lower_priority = BlockedStatus("789")
    with (
        patch("charm_refresh.Machines", MagicMock(return_value=refresh_mock)),
        patch(
            "charm_refresh.Machines.unit_status_lower_priority", return_value=BlockedStatus("789")
        ),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)

        assert state_out.unit_status != BlockedStatus("789")
