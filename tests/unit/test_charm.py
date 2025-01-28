#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import MagicMock, patch

import ops
import yaml
from ops import testing

from charm import EtcdOperatorCharm
from core.models import Member
from literals import CLIENT_PORT, INTERNAL_USER, INTERNAL_USER_PASSWORD_CONFIG, PEER_RELATION

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

MEMBER_LIST_DICT = {
    "charmed-etcd0": Member(
        id="1",
        name="etcd-test-1",
        peer_urls=["http://localhost:2380"],
        client_urls=["http://localhost:2379"],
    ),
    "charmed-etcd1": Member(
        id="2",
        name="etcd-test-2",
        peer_urls=["http://localhost:2381"],
        client_urls=["http://localhost:2380"],
    ),
}


def test_install_failure_blocked_status():
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State()

    with patch("workload.EtcdWorkload.install", return_value=False):
        state_out = ctx.run(ctx.on.install(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("unable to install etcd snap")


def test_internal_user_creation():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    restart_relation = testing.PeerRelation(id=2, endpoint="restart")

    state_in = testing.State(relations={relation, restart_relation}, leader=True)
    state_out = ctx.run(ctx.on.leader_elected(), state_in)
    secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
    assert secret_out.latest_content.get(f"{INTERNAL_USER}-password")


def test_start():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(leader=True)

    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    # non-leader units should not start directly
    state_in = testing.State(leader=False)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status != ops.ActiveStatus()

    # if authentication cannot be enabled, the charm should be blocked
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="test")),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.BlockedStatus(
            "failed to enable authentication in etcd"
        )

    # if the cluster is new, the leader should immediately start and enable auth
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_app_data={})
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="OK")),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()
        assert state_out.get_relation(1).local_app_data.get("authentication") == "enabled"

    # if the cluster already exists, the leader should not start but wait for being added as member
    relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_app_data={"cluster_state": "existing"}
    )
    state_in = testing.State(relations={relation}, leader=True)
    with patch("workload.EtcdWorkload.write_file"):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status != ops.ActiveStatus()
        assert state_out.get_relation(1).local_unit_data.get("state") != "started"

    # if the etcd daemon can't start, the charm should display blocked status
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=False),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("etcd service not running")


def test_update_status():
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State()

    with patch("workload.EtcdWorkload.alive", return_value=False):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("etcd service not running")

    # test data storage
    # Set up storage with some content:
    data_storage = testing.Storage("data")
    (data_storage.get_filesystem(ctx) / "myfile.data").write_text("helloworld")

    with patch("workload.EtcdWorkload.alive", return_value=True):
        with ctx(ctx.on.update_status(), testing.State(storages=[data_storage])) as context:
            data = context.charm.model.storages["data"][0]
            data_loc = data.location
            data_path = data_loc / "myfile.data"
            assert data_path.exists()
            assert data_path.read_text() == "helloworld"

            test_file = data_loc / "test.txt"
            test_file.write_text("test_line")

    # Verify that writing the file did work as expected.
    assert (data_storage.get_filesystem(ctx) / "test.txt").read_text() == "test_line"


def test_peer_relation_created():
    test_data = {"hostname": "my_hostname", "ip": "my_ip"}

    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(relations={relation})
    with (
        patch("managers.cluster.ClusterManager.get_host_mapping", return_value=test_data),
        patch("managers.cluster.ClusterManager.leader"),
    ):
        state_out = ctx.run(ctx.on.relation_created(relation=relation), state_in)
        assert state_out.get_relation(1).local_unit_data.get("hostname") == test_data["hostname"]


def test_get_leader():
    test_ip = "10.54.237.119"
    member_id = 11187096354790748301
    test_data = {
        "Endpoint": f"http://{test_ip}:{CLIENT_PORT}",
        "Status": {
            "header": {
                "cluster_id": 9102535641521235766,
                "member_id": member_id,
            },
            "version": "3.4.22",
            "leader": member_id,
        },
    }

    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(relations={relation})
    with patch("managers.cluster.EtcdClient.get_endpoint_status", return_value=test_data):
        with ctx(ctx.on.relation_joined(relation=relation), state_in) as context:
            assert context.charm.cluster_manager.leader == hex(member_id)[2:]


def test_config_changed():
    secret_key = "root"
    secret_value = "123"
    secret_content = {secret_key: secret_value}
    secret = ops.testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State(
        secrets=[secret],
        config={INTERNAL_USER_PASSWORD_CONFIG: secret.id},
        relations={relation},
        leader=True,
    )

    with patch("subprocess.run"):
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
        assert secret_out.latest_content.get(f"{INTERNAL_USER}-password") == secret_value


def test_unit_removal():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "cluster_state": "existing",
            "authentication": "enabled",
            "cluster_members": "abc",
        },
    )
    data_storage = testing.Storage("data")
    state_in = testing.State(storages=[data_storage], relations={relation})

    # test the happy path
    with (
        patch("common.client.EtcdClient.member_list", return_value=MEMBER_LIST_DICT),
        patch("subprocess.run"),
        patch("workload.EtcdWorkload.stop"),
        patch("managers.cluster.ClusterManager.leader"),
    ):
        state_out = ctx.run(ctx.on.storage_detaching(data_storage), state_in)
        assert state_out.unit_status == ops.BlockedStatus("unit removed from cluster")
        assert state_out.get_relation(1).local_app_data.get("authentication")
        assert state_out.get_relation(1).local_app_data.get("cluster_state")
        assert state_out.get_relation(1).local_app_data.get("cluster_members")

    # in case of error when removing the member, unit should be in blocked state with error
    with (
        patch("common.client.EtcdClient.member_list", return_value=MEMBER_LIST_DICT),
        patch("managers.cluster.ClusterManager.leader"),
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="remove member")),
        # mock the `wait` in tenacity.retry to avoid delay in retrying
        patch("tenacity.nap.time.sleep", MagicMock()),
        patch("workload.EtcdWorkload.stop"),
    ):
        state_out = ctx.run(ctx.on.storage_detaching(data_storage), state_in)
        assert state_out.unit_status == ops.BlockedStatus("cluster management error")

    # if all units are removed, cluster state data should be cleaned from application databag
    state_in = testing.State(
        storages=[data_storage], relations={relation}, planned_units=0, leader=True
    )
    with (
        patch("common.client.EtcdClient.member_list", return_value=MEMBER_LIST_DICT),
        patch("managers.cluster.ClusterManager.leader"),
        patch("subprocess.run"),
        patch("workload.EtcdWorkload.stop"),
    ):
        state_out = ctx.run(ctx.on.storage_detaching(data_storage), state_in)
        assert state_out.unit_status == ops.BlockedStatus("unit removed from cluster")
        assert not state_out.get_relation(1).local_app_data.get("authentication")
        assert not state_out.get_relation(1).local_app_data.get("cluster_state")
        assert not state_out.get_relation(1).local_app_data.get("cluster_members")
