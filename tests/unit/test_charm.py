#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import patch

import yaml
import ops
from ops import testing

from charm import EtcdOperatorCharm
from literals import CLIENT_PORT, INTERNAL_USER, PEER_RELATION

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def test_install_failure_blocked_status():
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State()

    with patch("workload.EtcdWorkload.install", return_value=False):
        state_out = ctx.run(ctx.on.install(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("unable to install etcd snap")


def test_internal_user_creation():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    state_in = testing.State(relations={relation}, leader=True)
    with patch("workload.EtcdWorkload.install", return_value=True):
        state_out = ctx.run(ctx.on.install(), state_in)
        secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
        assert secret_out.latest_content.get(f"{INTERNAL_USER}-password")


def test_start():
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State()

    # without peer relation the charm should not start
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("managers.cluster.ClusterManager.get_leader"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.MaintenanceStatus("no peer relation available")

    # with peer relation, it should go to active status
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(relations={relation})

    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("managers.cluster.ClusterManager.get_leader"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    # if the etcd daemon can't start, the charm should display blocked status
    with (
        patch("workload.EtcdWorkload.alive", return_value=False),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("managers.cluster.ClusterManager.get_leader"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("etcd service not running")


def test_update_status():
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State()

    with patch("workload.EtcdWorkload.alive", return_value=False):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("etcd service not running")


def test_peer_relation_created():
    test_data = {"hostname": "my_hostname", "ip": "my_ip"}

    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(relations={relation})
    with (
        patch("managers.cluster.ClusterManager.get_host_mapping", return_value=test_data),
        patch("managers.cluster.ClusterManager.get_leader"),
    ):
        state_out = ctx.run(ctx.on.relation_created(relation=relation), state_in)
        assert state_out.get_relation(1).local_unit_data.get("hostname") == test_data["hostname"]


def test_get_leader():
    test_ip = "10.54.237.119"
    test_data = {
        "Endpoint": f"http://{test_ip}:{CLIENT_PORT}",
        "Status": {
            "header": {
                "cluster_id": 9102535641521235766,
                "member_id": 11187096354790748301,
            },
            "version": "3.4.22",
            "leader": 11187096354790748301,
        },
    }

    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_unit_data={"ip": test_ip})
    state_in = testing.State(relations={relation})
    with patch("managers.cluster.EtcdClient.get_endpoint_status", return_value=test_data):
        with ctx(ctx.on.relation_joined(relation=relation), state_in) as context:
            assert context.charm.cluster_manager.get_leader() == f"http://{test_ip}:{CLIENT_PORT}"
