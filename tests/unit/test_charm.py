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
from requests.exceptions import RequestException

from charm import EtcdOperatorCharm
from common.exceptions import (
    EtcdClusterManagementError,
    EtcdServiceError,
    EtcdUserManagementError,
)
from core.models import Member
from literals import (
    CLIENT_PORT,
    INTERNAL_USER,
    INTERNAL_USER_PASSWORD_CONFIG,
    PEER_RELATION,
    STATUS_PEERS_RELATION,
    TLSState,
    TuningOptions,
)
from statuses import ClusterStatuses, ConfigStatuses, EtcdServiceStatuses, TLSStatuses

from .helpers import status_is

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


def test_install_failure():
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State()

    with patch("workload.EtcdWorkload.install", side_effect=EtcdServiceError()):
        with raises(testing.errors.UncaughtCharmError) as e:
            ctx.run(ctx.on.install(), state_in)
        assert isinstance(e.value.__cause__, EtcdServiceError)


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
    status_peer_relation = testing.PeerRelation(id=2, endpoint=STATUS_PEERS_RELATION)
    state_in = testing.State(leader=True, relations={relation, status_peer_relation})

    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("subprocess.run"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()

    # raise if starting the service fails
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.is_reachable", return_value=False),
        patch("subprocess.run"),
    ):
        with raises(testing.errors.UncaughtCharmError) as e:
            ctx.run(ctx.on.start(), state_in)

        assert isinstance(e.value.__cause__, EtcdServiceError)

    # non-leader units should not start directly
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
        },
    )
    state_in = testing.State(leader=False, relations={relation, status_peer_relation})
    with (
        patch("workload.EtcdWorkload.alive", return_value=False),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("workload.EtcdWorkload.start") as start,
        patch("subprocess.run"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert status_is(state_out, ClusterStatuses.CLUSTER_NOT_JOINED.value)
        start.assert_not_called()

    # if authentication cannot be enabled, the charm should error out
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
    )
    state_in = testing.State(relations={relation, status_peer_relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="test")),
    ):
        with raises(testing.errors.UncaughtCharmError) as e:
            state_out = ctx.run(ctx.on.start(), state_in)
            assert not state_out.get_relation(1).local_app_data.get("authentication") == "enabled"

        assert isinstance(e.value.__cause__, EtcdUserManagementError)

    # if the cluster is new, the leader should immediately start and enable auth
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_app_data={})
    state_in = testing.State(relations={relation, status_peer_relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.exists", return_value=False),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="OK")),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()
        assert state_out.get_relation(1).local_app_data.get("authentication") == "enabled"
        assert state_out.get_relation(1).local_app_data.get("cluster_state") == "existing"

    # if the cluster is reusing storage, the workload should start and broadcast its peer URL
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_app_data={})
    state_in = testing.State(relations={relation, status_peer_relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.exists", return_value=True),
        patch("workload.EtcdWorkload.write_file") as write_config,
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run", return_value=CompletedProcess(returncode=0, args=[], stdout="OK")),
        patch("managers.cluster.ClusterManager.broadcast_peer_url") as broadcast_peer_url,
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        # 1st call: set `force-new-cluster` to `True`, 2nd: reset `force-new-cluster` to `False`
        assert write_config.call_count == 2
        broadcast_peer_url.assert_called()
        assert state_out.unit_status == ops.ActiveStatus()
        assert state_out.get_relation(1).local_app_data.get("authentication") == "enabled"
        assert state_out.get_relation(1).local_app_data.get("cluster_state") == "existing"

    # if the cluster already exists, the leader should not start but wait for being added as member
    relation = testing.PeerRelation(
        id=1, endpoint=PEER_RELATION, local_app_data={"cluster_state": "existing"}
    )
    state_in = testing.State(relations={relation, status_peer_relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status != ops.ActiveStatus()
        assert state_out.get_relation(1).local_unit_data.get("state") != "started"

    # if the etcd daemon can't start, the charm should raise an exception
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(relations={relation, status_peer_relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.alive", return_value=False),
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("workload.EtcdWorkload.start"),
        patch("subprocess.run"),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert status_is(state_out, EtcdServiceStatuses.SERVICE_NOT_RUNNING.value)

    # non leader waiting promoted
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://ip0:2380,charmed-etcd1=http://ip1:2380",
            "authentication": "enabled",
        },
        local_unit_data={"hostname": "charmed-etcd0", "private_ip": "ip0"},
    )
    state_in = testing.State(relations={relation, status_peer_relation})
    with (
        patch("workload.EtcdWorkload.start") as start,
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()
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
        local_unit_data={"hostname": "charmed-etcd0", "private_ip": "ip0", "state": "started"},
    )
    state_in = testing.State(relations={relation, status_peer_relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.start") as start,
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
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
        local_unit_data={"hostname": "charmed-etcd0", "private_ip": "ip0", "state": "started"},
    )
    state_in = testing.State(relations={relation, status_peer_relation}, leader=True)
    with (
        patch("workload.EtcdWorkload.start") as start,
        patch("workload.EtcdWorkload.write_file"),
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("subprocess.run"),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == ops.ActiveStatus()
        assert state_out.get_relation(1).local_app_data.get("authentication") == "enabled"
        start.assert_called_once()

    # non leader must not start if auth not enabled
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://ip0:2380,charmed-etcd1=http://ip1:2380",
        },
        local_unit_data={"hostname": "charmed-etcd0", "private_ip": "ip0"},
    )
    state_in = testing.State(relations={relation, status_peer_relation})
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
            "cluster_members": "charmed-etcd0=http://:2380",
        },
    )
    status_peer_relation = testing.PeerRelation(
        id=2,
        endpoint=STATUS_PEERS_RELATION,
    )
    state_in = testing.State(relations={relation, status_peer_relation})

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
        assert status_is(state_out, EtcdServiceStatuses.SERVICE_NOT_RUNNING.value)

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
            "cluster_members": "charmed-etcd0=https://:2380",
        },
        local_unit_data={
            "tls_peer_state": TLSState.TLS.value,
            "tls_client_state": TLSState.TLS.value,
            "tls_client_certificates_expiring": "",
            "tls_peer_certificates_expiring": "",
        },
    )

    state_in = testing.State(relations={relation, status_peer_relation})

    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.clean_users"),
        patch(
            "workload.EtcdWorkload.exec",
            side_effect=CalledProcessError(returncode=1, cmd="openssl -checkend"),
        ),
    ):
        state_out = ctx.run(ctx.on.update_status(), state_in)
        assert status_is(state_out, TLSStatuses.TLS_PEER_CERTS_EXPIRING.value)
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
            "cluster_members": "charmed-etcd0=https://:2380",
        },
        local_unit_data={
            "tls_peer_state": TLSState.TLS.value,
            "tls_client_state": TLSState.TLS.value,
            "tls_client_certificates_expiring": "",
            "tls_peer_certificates_expiring": "",
        },
    )

    state_in = testing.State(relations={relation, status_peer_relation})

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
    status_peers_relation = testing.PeerRelation(
        id=2,
        endpoint=STATUS_PEERS_RELATION,
    )

    # leader should clean up inconsistent cluster members
    state_in = testing.State(relations={relation, status_peers_relation}, leader=True)
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
    state_in = testing.State(relations={relation, status_peers_relation}, leader=True)
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
        assert status_is(state_out, ClusterStatuses.CLUSTER_MANAGEMENT_ERROR.value)

    # no clean-up on non-leader units
    state_in = testing.State(relations={relation, status_peers_relation}, leader=False)
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
            "cluster_members": "charmed-etcd0=http://ip0:2380",
        },
        local_unit_data={"private_ip": "ip0", "state": "started"},
    )
    status_peer_relation = testing.PeerRelation(
        id=2,
        endpoint=STATUS_PEERS_RELATION,
    )
    state_in = testing.State(relations={relation, status_peer_relation})

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
        patch("managers.cluster.EtcdClient.get_metric", side_effect=RequestException()),
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
        assert status_is(state_out, ClusterStatuses.CLUSTER_FAILED.value)


def test_peer_relation_created():
    test_data = {"hostname": "my_hostname", "private_ip": "my_ip"}

    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    state_in = testing.State(relations={relation})
    with (
        patch("core.workload.WorkloadBase.get_host_mapping", return_value=test_data),
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

    current_config_file = {
        "election-timeout": 1000,
        "heartbeat-interval": 100,
        "quota-backend-bytes": 8589934592,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("subprocess.run"),
        patch("common.client.EtcdClient.member_list", return_value=MEMBER_LIST_DICT),
        patch("common.client.EtcdClient.broadcast_peer_url"),
        patch("workload.EtcdWorkload.write_file"),
        patch("managers.cluster.ClusterManager.restart_member", return_value=True),
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
        assert secret_out.latest_content.get(f"{INTERNAL_USER}-password") == secret_value


def test_set_config_options():
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"private_ip": "my_ip", "state": "started"},
        local_app_data={"cluster_state": "existing", "authentication": "enabled"},
    )
    ctx = testing.Context(EtcdOperatorCharm)

    # happy path - leader
    state_in = testing.State(
        config={
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: 5000,
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: 500,
        },
        relations={relation},
        leader=True,
    )

    current_config_file = {
        "election-timeout": 1000,
        "heartbeat-interval": 100,
        "quota-backend-bytes": 8589934592,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("charm.EtcdOperatorCharm.rolling_restart") as rolling_restart,
    ):
        ctx.run(ctx.on.config_changed(), state_in)
        rolling_restart.assert_called_once()

    # happy path - non-leader
    state_in = testing.State(
        config={
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: 5000,
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: 500,
        },
        relations={relation},
        leader=True,
    )

    current_config_file = {
        "election-timeout": 1000,
        "heartbeat-interval": 100,
        "quota-backend-bytes": 8589934592,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("charm.EtcdOperatorCharm.rolling_restart") as rolling_restart,
    ):
        ctx.run(ctx.on.config_changed(), state_in)
        rolling_restart.assert_called_once()

    # config values are equal to current config -> no restart triggered
    state_in = testing.State(
        config={
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: 5000,
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: 500,
        },
        relations={relation},
        leader=True,
    )

    current_config_file = {
        "election-timeout": 5000,
        "heartbeat-interval": 500,
        "quota-backend-bytes": 8589934592,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("charm.EtcdOperatorCharm.rolling_restart") as rolling_restart,
    ):
        ctx.run(ctx.on.config_changed(), state_in)
        rolling_restart.assert_not_called()

    # config values are invalid -> no restart triggered, blocked status
    state_in = testing.State(
        config={
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: 100,
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: 100,
        },
        relations={relation},
        leader=True,
    )

    current_config_file = {
        "election-timeout": 5000,
        "heartbeat-interval": 500,
        "quota-backend-bytes": 8589934592,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("charm.EtcdOperatorCharm.rolling_restart") as rolling_restart,
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        rolling_restart.assert_not_called()
        assert status_is(
            state_out,
            ConfigStatuses.TUNING_CONFIG_INVALID.value,
        )

    # config values are invalid -> no restart triggered, blocked status
    state_in = testing.State(
        config={
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: 50001,
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: 100,
        },
        relations={relation},
        leader=True,
    )

    current_config_file = {
        "election-timeout": 5000,
        "heartbeat-interval": 500,
        "quota-backend-bytes": 8589934592,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("charm.EtcdOperatorCharm.rolling_restart") as rolling_restart,
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        rolling_restart.assert_not_called()
        assert status_is(
            state_out,
            ConfigStatuses.TUNING_CONFIG_INVALID.value,
        )


def test_update_profile():
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={"private_ip": "my_ip", "state": "started"},
        local_app_data={"cluster_state": "existing", "authentication": "enabled"},
    )
    ctx = testing.Context(EtcdOperatorCharm)

    # testing to production
    state_in = testing.State(
        config={
            TuningOptions.ELECTION_TIMEOUT_CONFIG.value: 5000,
            TuningOptions.HEARTBEAT_INTERVAL_CONFIG.value: 500,
            "profile": "production",
        },
        relations={relation},
        leader=True,
    )

    current_config_file = {
        "election-timeout": 1000,
        "heartbeat-interval": 100,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("charm.EtcdOperatorCharm.rolling_restart") as rolling_restart,
    ):
        ctx.run(ctx.on.config_changed(), state_in)
        rolling_restart.assert_called_once()

    # production to testing
    state_in = testing.State(
        config={
            "profile": "testing",
        },
        relations={relation},
        leader=True,
    )

    current_config_file = {
        "election-timeout": 1000,
        "heartbeat-interval": 100,
        "quota-backend-bytes": 8589934592,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("charm.EtcdOperatorCharm.rolling_restart") as rolling_restart,
    ):
        ctx.run(ctx.on.config_changed(), state_in)
        rolling_restart.assert_called_once()

    # invalid profile
    state_in = testing.State(
        config={
            "profile": "invalid-profile",
        },
        relations={relation},
        leader=True,
    )

    current_config_file = {
        "election-timeout": 1000,
        "heartbeat-interval": 100,
        "quota-backend-bytes": 8589934592,
    }

    with (
        patch("workload.EtcdWorkload.load_yaml_file", return_value=current_config_file),
        patch("charm.EtcdOperatorCharm.rolling_restart") as rolling_restart,
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        rolling_restart.assert_not_called()
        assert status_is(
            state_out,
            ConfigStatuses.PROFILE_INVALID.value,
        )


def test_secret_changed():
    secret_key = "root"
    secret_value = "123"
    secret_content = {secret_key: secret_value}
    secret = ops.testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://:2380",
        },
    )
    status_peer_relation = testing.PeerRelation(
        id=2,
        endpoint=STATUS_PEERS_RELATION,
    )

    # test the happy path
    ctx = testing.Context(EtcdOperatorCharm)
    state_in = testing.State(
        secrets=[secret],
        config={INTERNAL_USER_PASSWORD_CONFIG: secret.id},
        relations={relation, status_peer_relation},
        leader=True,
    )
    with patch("subprocess.run"):
        state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_in)
        secret_out = state_out.get_secret(label=f"{PEER_RELATION}.{APP_NAME}.app")
        assert secret_out.latest_content.get(f"{INTERNAL_USER}-password") == secret_value

    # unhappy path: if the password update fails in etcd, charm status has to be blocked
    with patch("subprocess.run", side_effect=CalledProcessError(returncode=1, cmd="failed")):
        state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_in)

        assert status_is(state_out, ClusterStatuses.PASSWORD_UPDATE_FAILED.value, is_app=True)

    # no update should happen if the user-name is invalid, charm status has to be blocked
    secret_key = "invalid-user-name"
    secret_content = {secret_key: secret_value}
    secret = ops.testing.Secret(tracked_content=secret_content, remote_grants=APP_NAME)
    state_in = testing.State(
        secrets=[secret],
        config={INTERNAL_USER_PASSWORD_CONFIG: secret.id},
        relations={relation, status_peer_relation},
        leader=True,
    )
    with patch("common.client.EtcdClient.update_password") as update_password:
        state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_in)
        update_password.assert_not_called()
        assert status_is(
            state_out,
            ClusterStatuses.PASSWORD_UPDATE_FAILED.value,
            is_app=True,
        )


def test_peer_relation_joined():
    ctx = testing.Context(EtcdOperatorCharm)
    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={
            "hostname": "charmed-etcd0",
            "private_ip": "ip0",
        },
    )
    state_in = testing.State(relations={relation}, leader=True)
    state_out = ctx.run(ctx.on.relation_joined(relation=relation, remote_unit=1), state_in)
    assert "etcd_peers_relation_joined" in [event.name for event in state_out.deferred]

    relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={
            "hostname": "charmed-etcd0",
            "private_ip": "ip0",
        },
        peers_data={
            1: {
                "hostname": "charmed-etcd1",
                "private_ip": "ip1",
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
        local_unit_data={
            "hostname": "charmed-etcd0",
            "private_ip": "ip0",
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
                "private_ip": "ip1",
                "state": "started",
            },
        },
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
            "cluster_members": "charmed-etcd0=http://ip0:2380,charmed-etcd1=http://ip1:2380",
            "learning_member": "4477466968462020105",
        },
        local_unit_data={"hostname": "charmed-etcd0", "private_ip": "ip0", "state": "started"},
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
            "cluster_members": "charmed-etcd0=http://:2380",
        },
    )
    status_peer_relation = testing.PeerRelation(
        id=2,
        endpoint=STATUS_PEERS_RELATION,
    )
    data_storage = testing.Storage("data")
    state_in = testing.State(storages=[data_storage], relations={relation, status_peer_relation})

    # test the happy path
    with (
        patch("common.client.EtcdClient.member_list", return_value=MEMBER_LIST_DICT),
        patch("subprocess.run"),
        patch("workload.EtcdWorkload.stop"),
        patch("managers.cluster.ClusterManager.leader"),
        patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
    ):
        state_out = ctx.run(ctx.on.storage_detaching(data_storage), state_in)
        assert status_is(state_out, ClusterStatuses.REMOVED.value)
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
        storages=[data_storage],
        relations={relation, status_peer_relation},
        planned_units=0,
        leader=True,
    )
    with (
        patch("common.client.EtcdClient.member_list", return_value=MEMBER_LIST_DICT),
        patch("managers.cluster.ClusterManager.leader"),
        patch("subprocess.run"),
        patch("workload.EtcdWorkload.stop"),
    ):
        state_out = ctx.run(ctx.on.storage_detaching(data_storage), state_in)
        assert status_is(state_out, ClusterStatuses.REMOVED.value)
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

    # cluster didn't fail and not `force`
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_app_data={})
    state_in = testing.State(relations={peer_relation}, leader=True)
    with patch("managers.cluster.EtcdClient.get_metric", return_value="1"):
        with raises(testing.ActionFailed) as e:
            ctx.run(ctx.on.action("rebuild-cluster"), state_in)

            assert e.message == "Cluster has not failed. Use `force` to rebuild anyway."

    # cluster didn't fail and `force`
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_app_data={})
    state_in = testing.State(relations={peer_relation}, leader=True)
    with (
        patch("managers.cluster.EtcdClient.get_metric", return_value="1"),
        patch("workload.EtcdWorkload.stop"),
        patch("workload.EtcdWorkload.disable_service"),
    ):
        ctx.run(ctx.on.action("rebuild-cluster", params={"force": True}), state_in)

        assert ctx.action_results == {"result": "cluster rebuild in progress"}

    # error querying metrics server
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION, local_app_data={})
    state_in = testing.State(relations={peer_relation}, leader=True)
    with (
        patch("managers.cluster.EtcdClient.get_metric", side_effect=RequestException()),
        patch("workload.EtcdWorkload.stop"),
        patch("workload.EtcdWorkload.disable_service"),
    ):
        ctx.run(ctx.on.action("rebuild-cluster"), state_in)

        assert ctx.action_results == {"result": "cluster rebuild in progress"}


def test_rebuild_cluster_action_happy_path():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    status_peer_relation = testing.PeerRelation(
        id=2,
        endpoint=STATUS_PEERS_RELATION,
    )

    state_in = testing.State(relations={peer_relation, status_peer_relation}, leader=True)
    with (
        patch("managers.cluster.EtcdClient.get_metric", return_value="0"),
        patch("workload.EtcdWorkload.stop") as stop_etcd,
        patch("workload.EtcdWorkload.disable_service") as disable_etcd,
    ):
        state_out = ctx.run(ctx.on.action("rebuild-cluster"), state_in)

        stop_etcd.assert_called_once()
        disable_etcd.assert_called_once()
        assert ctx.action_results == {"result": "cluster rebuild in progress"}
        assert status_is(state_out, ClusterStatuses.CLUSTER_REBUILD_IN_PROGRESS.value)
        assert state_out.get_relation(1).local_app_data.get("rebuild_cluster")
        assert not state_out.get_relation(1).local_unit_data.get("state") == "started"

    # ensure action can be run multiple times
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True"},
    )
    state_in = testing.State(relations={peer_relation, status_peer_relation}, leader=True)
    with (
        patch("managers.cluster.EtcdClient.get_metric", return_value="0"),
        patch("workload.EtcdWorkload.stop") as stop_etcd,
        patch("workload.EtcdWorkload.disable_service") as disable_etcd,
    ):
        state_out = ctx.run(ctx.on.action("rebuild-cluster"), state_in)

        assert ctx.action_results == {"result": "cluster rebuild in progress"}
        assert status_is(state_out, ClusterStatuses.CLUSTER_REBUILD_IN_PROGRESS.value)
        assert state_out.get_relation(1).local_app_data.get("rebuild_cluster")


def test_rebuild_cluster_workflow_synchronisation():
    ctx = testing.Context(EtcdOperatorCharm, app_name="etcd", unit_id=0)

    # after leader initiated workflow (on_action), non-leaders will stop
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True", "cluster_state": "existing"},
        local_unit_data={"state": "started"},
    )
    state_in = testing.State(relations={peer_relation}, leader=False)

    with (
        patch("workload.EtcdWorkload.stop") as stop_etcd,
        patch("workload.EtcdWorkload.disable_service") as disable_etcd,
        patch("workload.EtcdWorkload.remove_directory") as remove_data,
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=peer_relation), state_in)

        stop_etcd.assert_called_once()
        disable_etcd.assert_called_once()
        remove_data.assert_called_once()
        assert not state_out.get_relation(1).local_unit_data.get("state") == "started"

    # after all units are stopped, the leader initialises a new cluster and starts
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"rebuild_cluster": "True", "cluster_state": "existing"},
        local_unit_data={},
    )
    state_in = testing.State(relations={peer_relation}, leader=True)

    with (
        patch("workload.EtcdWorkload.write_file") as write_config,
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("workload.EtcdWorkload.start") as start_etcd,
        patch("workload.EtcdWorkload.enable_service") as enable_etcd,
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=peer_relation), state_in)

        # 1st call: set `force-new-cluster` to `True`, 2nd: reset `force-new-cluster` to `False`
        assert write_config.call_count == 2
        start_etcd.assert_called_once()
        enable_etcd.assert_called_once()
        assert state_out.get_relation(1).local_unit_data.get("state") == "started"
        assert state_out.get_relation(1).local_unit_data.get("rebuild_completed") == "True"

    # after leader has initialised and non-leader unit was added, it starts
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "rebuild_cluster": "True",
            "cluster_state": "existing",
            "cluster_members": "etcd0=http://ip0:2380,etcd1=http://ip1:2380",
        },
        local_unit_data={"hostname": "etcd0", "private_ip": "ip0"},
    )
    state_in = testing.State(relations={peer_relation}, leader=False)

    with (
        patch("workload.EtcdWorkload.write_file") as write_config,
        patch("workload.EtcdWorkload.is_reachable", return_value=True),
        patch("workload.EtcdWorkload.start") as start_etcd,
        patch("workload.EtcdWorkload.enable_service") as enable_etcd,
    ):
        state_out = ctx.run(ctx.on.relation_changed(relation=peer_relation), state_in)

        write_config.assert_called_once()
        start_etcd.assert_called_once()
        enable_etcd.assert_called_once()
        assert state_out.get_relation(1).local_unit_data.get("state") == "started"
        assert state_out.get_relation(1).local_unit_data.get("rebuild_completed") == "True"

    # after all units started, leader performs health check and completes workflow
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "rebuild_cluster": "True",
            "cluster_state": "existing",
            "cluster_members": "etcd0=http://ip0:2380,etcd1=http://ip1:2380",
        },
        local_unit_data={
            "state": "started",
            "rebuild_completed": "True",
            "hostname": "etcd0",
            "private_ip": "ip0",
        },
    )
    state_in = testing.State(relations={peer_relation}, leader=True)

    with patch("managers.cluster.ClusterManager.is_healthy") as health_check:
        state_out = ctx.run(ctx.on.relation_changed(relation=peer_relation), state_in)

        health_check.assert_called_once()
        assert not state_out.get_relation(1).local_app_data.get("rebuild_cluster") == "True"
