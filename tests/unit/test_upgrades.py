#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, patch

import pytest
from charm_refresh import PrecheckFailed
from ops import testing

from charm import EtcdOperatorCharm
from common.exceptions import EtcdUpgradeError
from events.refresh import is_workload_compatible
from literals import PEER_RELATION
from src.events.refresh import MachinesEtcdRefresh


@pytest.mark.parametrize(
    "old_version,new_version,expected",
    [
        ("3.6.0", "3.6.1", True),  # Patch upgrade allowed
        ("3.6.0", "3.7.0", False),  # Minor upgrade not allowed
        ("3.6.0", "4.0.0", False),  # Major upgrade not allowed
        ("3.6.0", "3.5.0", False),  # Downgrade not allowed
        ("invalid", "3.6.0", False),  # Invalid version format
        ("3.6.0", "invalid", False),  # Invalid version format
    ],
)
def test_is_workload_compatible(old_version: str, new_version: str, expected: bool) -> None:
    assert is_workload_compatible(old_version, new_version) == expected


def test_pre_refresh_checks() -> None:
    ctx = testing.Context(EtcdOperatorCharm)

    # check fails because backup in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"backup_id": "XYZ"},
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

            assert str(e.value) == "Backup in progress"

    # check fails because restore in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"restore_id": "XYZ"},
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

            assert str(e.value) == "Restore in progress"

    # check fails because unit not healthy
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

    # check fails because cluster-rebuild in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True"},
    )

    state_in = testing.State(relations={peer_relation})

    with patch("managers.cluster.ClusterManager.is_healthy", return_value=True):
        with ctx(ctx.on.relation_changed(relation=peer_relation), state_in) as manager:
            charm: EtcdOperatorCharm = manager.charm

            # Mock the refresh constructor to avoid version checks
            with patch("events.refresh.MachinesEtcdRefresh.__init__", return_value=None):
                refresh = MachinesEtcdRefresh.__new__(MachinesEtcdRefresh)
                refresh.charm = charm
                with pytest.raises(PrecheckFailed) as e:
                    refresh.run_pre_refresh_checks_after_1_unit_refreshed()

                assert str(e.value) == "Cluster rebuild in progress"

    # check fails because TLS transition in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"tls_client_state": "to-tls"},
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

            assert str(e.value) == "TLS transition is in progress"

    # check fails because TLS CA rotation in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"tls_peer_ca_rotation": "new-ca-detected"},
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

            assert str(e.value) == "TLS CA rotation is in progress"


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
