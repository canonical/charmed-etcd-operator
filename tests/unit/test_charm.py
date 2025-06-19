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

    # leader should clean up inconsistent cluster members
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("common.client.EtcdClient.member_list", return_value=cluster_member_list),
        patch("common.client.EtcdClient.remove_member") as remove_member,
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("workload.EtcdWorkload.exec", return_value=CompletedProcess(returncode=0, args=[])),
        patch(
            "managers.cluster.ClusterManager.update_cluster_member_state"
        ) as update_cluster_member_state,
    ):
        ctx.run(ctx.on.update_status(), state_in)
        remove_member.assert_called()
        update_cluster_member_state.assert_called_once()

    # error case: clean up fails
    state_in = testing.State(relations={relation}, leader=True)
    with (
        patch("common.client.EtcdClient.member_list", return_value=cluster_member_list),
        patch(
            "common.client.EtcdClient.remove_member", side_effect=EtcdClusterManagementError()
        ) as remove_member,
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("workload.EtcdWorkload.exec", return_value=CompletedProcess(returncode=0, args=[])),
        patch(
            "managers.cluster.ClusterManager.update_cluster_member_state"
        ) as update_cluster_member_state,
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        remove_member.assert_called()
        update_cluster_member_state.assert_not_called()
        assert state_out.unit_status == ops.BlockedStatus("cluster management error")

    # no clean-up on non-leader units
    state_in = testing.State(relations={relation}, leader=False)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("workload.EtcdWorkload.exec", return_value=CompletedProcess(returncode=0, args=[])),
        patch("common.client.EtcdClient.remove_member") as remove_member,
        patch(
            "managers.cluster.ClusterManager.update_cluster_member_state"
        ) as update_cluster_member_state,
    ):
        ctx.run(ctx.on.update_status(), state_in)
        remove_member.assert_not_called()
        update_cluster_member_state.assert_not_called()


def test_cluster_majority_failure():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
        },
        local_unit_data={"ip": "ip0"},
    )
    state_in = testing.State(relations={relation})

    # happy path: metric "etcd_server_has_leader" == 1
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("managers.cluster.EtcdClient.get_metric", return_value="1"),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    # error querying the metrics (metric not found)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("managers.cluster.EtcdClient.get_metric", return_value=None),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    # error querying the metrics (request error)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("managers.cluster.EtcdClient.get_metric", side_effect=RuntimeError()),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    # cluster has failed
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch("managers.cluster.EtcdClient.get_metric", return_value="0"),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert state_out.unit_status == ops.BlockedStatus(
            "Cluster failure - majority of cluster members lost"
        )


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
        patch("managers.cluster.ClusterManager.get_version", return_value="3.6.0"),
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


def test_rebuild_cluster_action_error_cases():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    # ensure action fails if run on non-leader unit
    state_in = testing.State(relations={peer_relation}, leader=False)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("rebuild-cluster"), state_in)

        assert e.message == "Action must be performed on the leader unit."

    # ensure action fails if backup is in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"backup_id": "xyz"},
    )
    state_in = testing.State(relations={peer_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("rebuild-cluster"), state_in)

        assert e.message == "Backup in progress, cannot perform action."

    # ensure action fails if restore is in progress
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"restore_id": "xyz"},
    )
    state_in = testing.State(relations={peer_relation}, leader=True)
    with raises(testing.ActionFailed) as e:
        ctx.run(ctx.on.action("rebuild-cluster"), state_in)

        assert e.message == "Restore in progress, cannot perform action."


def test_rebuild_cluster_action_happy_path():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)

    state_in = testing.State(relations={peer_relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.stop") as stop_etcd,
        patch("workload.EtcdWorkload.disable_service") as disable_etcd,
    ):
        state_out = ctx.run(ctx.on.action("rebuild-cluster"), state_in)

        stop_etcd.assert_called_once()
        disable_etcd.assert_called_once()
        assert ctx.action_results == {"result": "cluster rebuild in progress"}
        assert state_out.unit_status == ops.BlockedStatus(
            "Rebuilding with new cluster configuration..."
        )
        assert state_out.get_relation(1).local_app_data.get("rebuild_cluster")
        assert not state_out.get_relation(1).local_unit_data.get("state") == "started"

    # ensure action can be run multiple times
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True"},
    )
    state_in = testing.State(relations={peer_relation}, leader=True)
    state_out = ctx.run(ctx.on.action("rebuild-cluster"), state_in)

    assert ctx.action_results == {"result": "cluster rebuild in progress"}
    assert state_out.unit_status == ops.BlockedStatus(
        "Rebuilding with new cluster configuration..."
    )
    assert state_out.get_relation(1).local_app_data.get("rebuild_cluster")


def test_rebuild_cluster_workflow_synchronisation():
    ctx = testing.Context(EtcdOperatorCharm)

    # after leader initiated workflow (on_action), non-leaders will stop
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True", "initial_cluster_state": "existing"},
        local_unit_data={"state": "started"},
    )
    state_in = testing.State(relations={peer_relation}, leader=False)

    with (
        patch("workload.EtcdWorkload.stop") as stop_etcd,
        patch("workload.EtcdWorkload.disable_service") as disable_etcd,
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=peer_relation), state_in)

        stop_etcd.assert_called_once()
        disable_etcd.assert_called_once()
        assert not state_out.get_relation(1).local_unit_data.get("state") == "started"

    # after all units are stopped, the leader initialises a new cluster and starts
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True", "initial_cluster_state": "existing"},
        local_unit_data={},
    )
    state_in = testing.State(relations={peer_relation}, leader=True)

    with (
        patch("workload.EtcdWorkload.write_file") as write_config,
        patch("workload.EtcdWorkload.start") as start_etcd,
        patch("workload.EtcdWorkload.enable_service") as enable_etcd,
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=peer_relation), state_in)

        write_config.assert_called_once()
        start_etcd.assert_called_once()
        enable_etcd.assert_called_once()
        assert state_out.get_relation(1).local_unit_data.get("state") == "started"
        assert state_out.get_relation(1).local_app_data.get("initial_cluster_state") == "new"

    # after leader has initialised, non-leaders start
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True", "initial_cluster_state": "new"},
        local_unit_data={},
    )
    state_in = testing.State(relations={peer_relation}, leader=False)

    with (
        patch("workload.EtcdWorkload.write_file") as write_config,
        patch("workload.EtcdWorkload.start") as start_etcd,
        patch("workload.EtcdWorkload.enable_service") as enable_etcd,
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=peer_relation), state_in)

        write_config.assert_called_once()
        start_etcd.assert_called_once()
        enable_etcd.assert_called_once()
        assert state_out.get_relation(1).local_unit_data.get("state") == "started"
        assert state_out.get_relation(1).local_app_data.get("initial_cluster_state") == "new"

    # after all units started, leader performs health check and completes workflow
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True", "initial_cluster_state": "new"},
        local_unit_data={},
    )
    state_in = testing.State(relations={peer_relation}, leader=False)

    with patch("managers.cluster.ClusterManager.is_healthy") as health_check:
        state_out = ctx.run(ctx.on.relation_changed(relation=peer_relation), state_in)

        health_check.assert_called_once()
        assert state_out.get_relation(1).local_unit_data.get("state") == "started"
        assert state_out.get_relation(1).local_app_data.get("initial_cluster_state") == "existing"
        assert not state_out.get_relation(1).local_app_data.get("rebuild_cluster") == "True"
