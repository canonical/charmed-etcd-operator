#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import MagicMock, patch

import ops
import yaml
from ops import testing
from pytest import raises

from charm import EtcdOperatorCharm
from common.exceptions import (
    EtcdClusterManagementError,
    EtcdUserManagementError,
)
from core.models import Member
from literals import (
    CLIENT_PORT,
    INTERNAL_USER,
    INTERNAL_USER_PASSWORD_CONFIG,
    PEER_RELATION,
    TLSState,
)

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
    state_in = testing.State(leader=True, relations={relation})

    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    # non-leader units should not start directly
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
        },
    )
    state_in = testing.State(leader=False, relations={relation})
    with (
        patch("workload.EtcdWorkload.alive", return_value=False),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start") as start,
        patch("subprocess.run"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.MaintenanceStatus("Waiting to join cluster")
        start.assert_not_called()

    # if authentication cannot be enabled, the charm should error out
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="test")),
    ):
        with raises(testing.errors.UncaughtCharmError) as e:
            state_out = ctx.run(ctx.on.start(), state_in)
            assert not state_out.get_relation(1).local_app_data.get("authentication") == "enabled"

        assert isinstance(e.value.__cause__, EtcdUserManagementError)

    # if the cluster is new, the leader should immediately start and enable auth
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_app_data={})
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.exists", return_value=False),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="OK")),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()
        assert state_out.get_relation(1).local_app_data.get("authentication") == "enabled"
        assert state_out.get_relation(1).local_app_data.get("cluster_state") == "existing"

    # if the cluster is reusing storage, the workload should start and broadcast its peer URL
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_app_data={})
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.exists", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="OK")),
        patch("managers.cluster.ClusterManager.broadcast_peer_url") as broadcast_peer_url,
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        broadcast_peer_url.assert_called()
        assert state_out.unit_status == ops.ActiveStatus()
        assert state_out.get_relation(1).local_app_data.get("authentication") == "enabled"
        assert state_out.get_relation(1).local_app_data.get("cluster_state") == "existing"

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

    # non leader waiting promoted
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://ip0:2380,charmed-etcd1=http://ip1:2380",
            "authentication": "enabled",
        },
        local_unit_data={"hostname": "charmed-etcd0", "ip": "ip0"},
    )
    state_in = testing.State(relations={relation})
    with (
        patch("workload.EtcdWorkload.start") as start,
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.MaintenanceStatus("Waiting for etcd to start...")
        assert state_out.get_relation(1).local_unit_data.get("state") == "started"
        start.assert_called_once()

    # leader started but auth not enabled -> retry -> fails -> raise
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://ip0:2380",
        },
        local_unit_data={"hostname": "charmed-etcd0", "ip": "ip0", "state": "started"},
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.start") as start,
        patch("workload.EtcdWorkload.write_file"),
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="test")),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert not state_out.get_relation(1).local_app_data.get("authentication") == "enabled"

        start.assert_not_called()

    # leader started but auth not enabled -> retry -> success
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://ip0:2380",
        },
        local_unit_data={"hostname": "charmed-etcd0", "ip": "ip0", "state": "started"},
    )
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.start") as start,
        patch("workload.EtcdWorkload.write_file"),
        patch("subprocess.run"),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()
        assert state_out.get_relation(1).local_app_data.get("authentication") == "enabled"
        start.assert_not_called()

    # non leader must not start if auth not enabled
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://ip0:2380,charmed-etcd1=http://ip1:2380",
        },
        local_unit_data={"hostname": "charmed-etcd0", "ip": "ip0"},
    )
    state_in = testing.State(relations={relation})
    with (
        patch("workload.EtcdWorkload.start") as start,
        patch("workload.EtcdWorkload.write_file"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert not state_out.get_relation(1).local_unit_data.get("state") == "started"

        start.assert_not_called()


def test_update_status():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
        },
    )
    state_in = testing.State(relations={relation})

    # restart workload if not running
    with (
        patch("workload.EtcdWorkload.alive", return_value=False),
        patch("managers.cluster.ClusterManager.restart_member", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    # failed restart should block status
    with (
        patch("workload.EtcdWorkload.alive", return_value=False),
        patch("managers.cluster.ClusterManager.restart_member", return_value=False),
        patch("managers.cluster.ClusterManager.clean_users"),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.BlockedStatus("etcd service not running")

    # test data storage
    # Set up storage with some content:
    data_storage = testing.Storage("data")
    (data_storage.get_filesystem(ctx) / "myfile.data").write_text("helloworld")

    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
    ):
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

    # test certificate expiry check fails
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
        },
        local_unit_data={
            "tls_peer_state": TLSState.TLS.value,
            "tls_client_state": TLSState.TLS.value,
            "tls_client_certificates_expiring": "",
            "tls_peer_certificates_expiring": "",
        },
    )

    state_in = testing.State(relations={relation})

    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch(
            "workload.EtcdWorkload.exec",
            side_effect=CalledProcessError(returncode=1, cmd="openssl -checkend"),
        ),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.MaintenanceStatus(
            "TLS client certificates expiring soon. Please ensure new certificates are provided."
        )
        assert (
            state_out.get_relation(1).local_unit_data.get("tls_client_certificates_expiring")
            == "True"
        )
        assert (
            state_out.get_relation(1).local_unit_data.get("tls_peer_certificates_expiring")
            == "True"
        )

    # test certificate expiry check successful
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
        },
        local_unit_data={
            "tls_peer_state": TLSState.TLS.value,
            "tls_client_state": TLSState.TLS.value,
            "tls_client_certificates_expiring": "",
            "tls_peer_certificates_expiring": "",
        },
    )

    state_in = testing.State(relations={relation})

    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("workload.EtcdWorkload.exec", return_value=CompletedProcess(returncode=0, args=[])),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()
        assert (
            state_out.get_relation(1).local_unit_data.get("tls_client_certificates_expiring") == ""
        )
        assert (
            state_out.get_relation(1).local_unit_data.get("tls_peer_certificates_expiring") == ""
        )


def test_removal_of_inconsistent_members():
    # this test is assuming the default relation has only one unit: remote/0
    cluster_member_list = {
        "remote0": Member(
            id="1",
            name="remote0",
            peer_urls=["http://ip:2380"],
            client_urls=["http://ip:2379"],
        ),
        "remote1": Member(
            id="2",
            name="remote1",
            peer_urls=["http://ip:2381"],
            client_urls=["http://ip:2380"],
        ),
    }

    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "cluster_state": "existing",
            "authentication": "enabled",
            "cluster_members": "remote0=http://ip0:2380,remote1=http://ip1:2380",
        },
    )
    state_in = testing.State(relations={relation})

    with (
        patch("common.client.EtcdClient.member_list", return_value=cluster_member_list),
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("workload.EtcdWorkload.exec", return_value=CompletedProcess(returncode=0, args=[])),
        patch(
            "managers.cluster.ClusterManager.update_cluster_member_state"
        ) as update_cluster_member_state,
    ):
        ctx.run(ctx.on.update_status(), state_in)
        update_cluster_member_state.assert_called_once()


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

    with (
        patch("subprocess.run"),
        patch("common.client.EtcdClient.member_list", return_value=MEMBER_LIST_DICT),
        patch("common.client.EtcdClient.broadcast_peer_url"),
        patch("workload.EtcdWorkload.write_file"),
        patch("managers.cluster.ClusterManager.restart_member", return_value=True),
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
        assert secret_out.latest_content.get(f"{INTERNAL_USER}-password") == secret_value


def test_secret_changed():
    secret_key = "root"
    secret_value = "123"
    secret_content = {secret_key: secret_value}
    secret = ops.testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    # test the happy path
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State(
        secrets=[secret],
        config={INTERNAL_USER_PASSWORD_CONFIG: secret.id},
        relations={relation},
        leader=True,
    )
    with patch("subprocess.run"):
        state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_in)
        secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
        assert secret_out.latest_content.get(f"{INTERNAL_USER}-password") == secret_value

    # unhappy path: if the password update fails in etcd, charm status has to be blocked
    with patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="failed")):
        state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_in)

        assert state_out.unit_status == ops.BlockedStatus("failed to update password")

    # no update should happen if the user-name is invalid, charm status has to be blocked
    secret_key = "invalid-user-name"
    secret_content = {secret_key: secret_value}
    secret = ops.testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    state_in = testing.State(
        secrets=[secret],
        config={INTERNAL_USER_PASSWORD_CONFIG: secret.id},
        relations={relation},
        leader=True,
    )
    with patch("subprocess.run") as run:
        state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_in)
        run.assert_not_called()
        assert state_out.unit_status == ops.BlockedStatus("failed to update password")


def test_peer_relation_joined():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        peers_data={
            0: {
                "hostname": "charmed-etcd0",
                "ip": "ip0",
            },
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    state_out = ctx.run(ctx.on.relation_joined(relation=relation, remote_unit=1), state_in)
    assert "etcd_peers_relation_joined" in [event.name for event in state_out.deferred]

    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        peers_data={
            0: {
                "hostname": "charmed-etcd0",
                "ip": "ip0",
            },
            1: {
                "hostname": "charmed-etcd1",
                "ip": "ip1",
            },
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    with patch(
        "common.client.EtcdClient._run_etcdctl",
        return_value=json.dumps(
            {
                "members": [
                    {
                        "name": "charmed-etcd0",
                        "ID": 11187096354790748301,
                        "clientURLs": ["http://ip0:2380"],
                        "peerURLs": ["http://ip0:2380"],
                    },
                    {
                        "ID": 4477466968462020105,
                        "clientURLs": ["http://ip1:2380"],
                        "peerURLs": ["http://ip1:2380"],
                    },
                ]
            }
        ),
    ):
        state_out = ctx.run(ctx.on.relation_joined(relation=relation, remote_unit=1), state_in)
        relation = state_out.get_relation(relation.id)
        assert relation.local_app_data.get("learning_member") == f"{4477466968462020105:x}"
        assert (
            relation.local_app_data.get("cluster_members")
            == "charmed-etcd0=http://ip0:2380,charmed-etcd1=http://ip1:2380"
        )


def test_peer_relation_changed():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        peers_data={
            0: {
                "hostname": "charmed-etcd0",
                "ip": "ip0",
            },
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    state_out = ctx.run(ctx.on.relation_joined(relation=relation, remote_unit=1), state_in)
    assert "etcd_peers_relation_joined" in [event.name for event in state_out.deferred]

    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        peers_data={
            1: {
                "hostname": "charmed-etcd1",
                "ip": "ip1",
                "state": "started",
            },
        },
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://ip0:2380,charmed-etcd1=http://ip1:2380",
            "learning_member": "4477466968462020105",
        },
        local_unit_data={"hostname": "charmed-etcd0", "ip": "ip0", "state": "started"},
    )
    state_in = testing.State(relations={relation}, leader=True)
    with patch(
        "common.client.EtcdClient._run_etcdctl",
        return_value=None,
    ) as promote_learning_member:
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)
        promote_learning_member.assert_called_once()
        assert state_out.deferred[0].name == "etcd_peers_relation_changed"

    with (
        patch("common.client.EtcdClient._run_etcdctl") as run_etcdctl,
        patch("managers.cluster.ClusterManager.update_cluster_member_state"),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5.18"),
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=relation), state_in)
        relation = state_out.get_relation(relation.id)
        assert relation.local_app_data.get("learning_member") is None
        assert (
            relation.local_app_data.get("cluster_members")
            == "charmed-etcd0=http://ip0:2380,charmed-etcd1=http://ip1:2380"
        )
        run_etcdctl_args = run_etcdctl.call_args[1]
        assert run_etcdctl_args["command"] == "member"
        assert run_etcdctl_args["subcommand"] == "promote"
        assert run_etcdctl_args["member"] == "4477466968462020105"
        assert (
            run_etcdctl_args["endpoints"] == "http://ip0:2379,http://ip1:2379"
            or run_etcdctl_args["endpoints"] == "http://ip1:2379,http://ip0:2379"
        )


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
        patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
    ):
        state_out = ctx.run(ctx.on.storage_detaching(data_storage), state_in)
        assert state_out.unit_status == ops.BlockedStatus("unit removed from cluster")
        assert state_out.get_relation(1).local_app_data.get("authentication")
        assert state_out.get_relation(1).local_app_data.get("cluster_state")
        assert state_out.get_relation(1).local_app_data.get("cluster_members")

    # in case of error when removing the member, unit should in error state
    with (
        patch("common.client.EtcdClient.member_list", return_value=MEMBER_LIST_DICT),
        patch("managers.cluster.ClusterManager.leader"),
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="remove member")),
        # mock the `wait` in tenacity.retry to avoid delay in retrying
        patch("tenacity.nap.time.sleep", MagicMock()),
        patch("workload.EtcdWorkload.stop"),
        patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
    ):
        with raises(testing.errors.UncaughtCharmError) as e:
            ctx.run(ctx.on.storage_detaching(data_storage), state_in)

        assert isinstance(e.value.__cause__, EtcdClusterManagementError)

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
