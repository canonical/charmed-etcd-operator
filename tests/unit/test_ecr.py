#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, patch

from ops import testing

from charm import EtcdOperatorCharm
from literals import EXTERNAL_CLIENTS_RELATION, TLSCARotationState

server_cert = MagicMock()
server_cert.ca.raw = "test_ca_server"


def test_add_ecr_new_user(cluster_tls_context):
    """Test adding an external client relation to the charm."""
    ctx, relations = cluster_tls_context

    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": """test_ca""",
            "common-name": "test-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value=None),
        patch("common.client.EtcdClient._run_etcdctl", return_value="success"),
        patch("workload.EtcdWorkload.write_file"),
        patch("events.tls.TLSEvents.collect_client_cas", return_value=["test_ca", "test_ca1"]),
        patch("managers.cluster.ClusterManager.restart_member"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
    ):
        charm: EtcdOperatorCharm = manager.charm
        manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users
        assert charm.state.cluster.managed_users[ecr_relation.id].common_name == "test-common-name"
        assert charm.state.cluster.managed_users[ecr_relation.id].ca_chain == "test_ca"
        assert ecr_relation.local_app_data["ca-chain"] == "test_ca_server"
        assert set(ecr_relation.local_app_data["endpoints"].split(",")) == set(
            "https://ip1:2379,https://ip2:2379,https://ip0:2379".split(",")
        )


def test_add_ecr_new_user_no_tls(cluster_no_tls_context):
    """Test adding an external client relation to the charm before TLS is enabled."""
    ctx, relations = cluster_no_tls_context
    ecr_relation = testing.Relation(
        id=len(relations) + 1,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": """test_ca""",
            "common-name": "test-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
    )

    server_cert = MagicMock()
    server_cert.ca.raw = "test_ca_server"

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        defered_event_names = [event.name for event in state_out.deferred]
        assert "common_name_updated" in defered_event_names
        assert "ca_chain_updated" in defered_event_names
        assert ecr_relation.id not in charm.state.cluster.managed_users


def test_add_ecr_new_user_incomplete_data_from_requirer(cluster_no_tls_context):
    """Test adding an external client relation to the charm with missing data from requirer."""
    ctx, relations = cluster_no_tls_context
    ecr_relation = testing.Relation(
        id=len(relations) + 1,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": """test_ca""",
            "common-name": "test-common-name",
            # "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
    )

    server_cert = MagicMock()
    server_cert.ca.raw = "test_ca_server"

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        defered_event_names = [event.name for event in state_out.deferred]
        assert "common_name_updated" in defered_event_names
        assert "ca_chain_updated" in defered_event_names
        assert ecr_relation.id not in charm.state.cluster.managed_users


def test_add_ecr_existing_user(cluster_tls_context):
    """Test adding an external client relation to the charm with the user already existing."""
    ctx, relations = cluster_tls_context
    ecr_relation = testing.Relation(
        id=len(relations) + 1,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": """test_ca""",
            "common-name": "test-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
    )

    server_cert = MagicMock()
    server_cert.ca.raw = "test_ca_server"

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value={"name": "test-common-name"}),
    ):
        charm: EtcdOperatorCharm = manager.charm
        manager.run()
        assert ecr_relation.id not in charm.state.cluster.managed_users


def test_ecr_update_common_name(cluster_tls_context):
    """Test updating the common name for an external client relation."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    old_common_name = "test-common-name"
    peer_relation.local_app_data["managed_users"] = (
        f'{{"managed_users":{{"5":{{"relation_id":5,"common_name":"{old_common_name}","ca_chain":"test_ca"}}}}}}'
    )

    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": """test_ca""",
            "common-name": "new-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
    )

    server_cert = MagicMock()
    server_cert.ca.raw = "test_ca_server"

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value=None),
        patch("common.client.EtcdClient._run_etcdctl", return_value="success"),
        patch("workload.EtcdWorkload.write_file"),
        patch("events.tls.TLSEvents.collect_client_cas", return_value=["test_ca", "test_ca1"]),
        patch("managers.cluster.ClusterManager.restart_member"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
        patch("managers.cluster.ClusterManager.remove_role") as remove_role,
        patch("managers.cluster.ClusterManager.remove_user") as remove_user,
    ):
        charm: EtcdOperatorCharm = manager.charm
        manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users
        assert charm.state.cluster.managed_users[ecr_relation.id].common_name == "new-common-name"
        remove_role.assert_called_once_with(old_common_name)
        remove_user.assert_called_once_with(old_common_name)


def test_ecr_update_ca_chain_while_rotation_happening(cluster_tls_context):
    """Test updating the CA chain for an external client relation while rotation is happening."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    old_ca = "test_ca"
    peer_relation.local_app_data["managed_users"] = (
        f'{{"managed_users":{{"5":{{"relation_id":5,"common_name":"test-common-name","ca_chain":"{old_ca}"}}}}}}'
    )

    peer_relation.local_unit_data["tls_client_ca_rotation"] = TLSCARotationState.NEW_CA_ADDED.value

    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": "new_ca",
            "common-name": "test-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": f'{{"ca-chain": "{old_ca}","common-name": "test-common-name", "keys-prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}}'
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert charm.state.cluster.managed_users[ecr_relation.id].ca_chain == old_ca
        assert "ca_chain_updated" in [event.name for event in state_out.deferred]


def test_ecr_relation_broken_leader(cluster_tls_context):
    """Test removing an external client relation from the charm."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    peer_relation.local_app_data["managed_users"] = (
        '{"managed_users":{"5":{"relation_id":5,"common_name":"test-common-name","ca_chain":"test_ca"}}}'
    )

    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": "new_ca",
            "common-name": "test-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": '{"ca-chain": "test_ca","common-name": "test-common-name", "keys-prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}'
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
    )

    with (
        ctx(ctx.on.relation_broken(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.remove_role") as remove_role,
        patch("common.client.EtcdClient.remove_user") as remove_user,
        patch("events.tls.TLSEvents.collect_client_cas") as collect_client_cas,
        patch("managers.tls.TLSManager.update_cas") as update_cas,
        patch("charm.EtcdOperatorCharm._restart") as restart,
    ):
        charm: EtcdOperatorCharm = manager.charm
        manager.run()
        assert ecr_relation.id not in charm.state.cluster.managed_users
        remove_role.assert_called_once_with("test-common-name")
        remove_user.assert_called_once_with("test-common-name")
        collect_client_cas.assert_called_once()
        update_cas.assert_called_once()
        restart.assert_called_once()


def test_ecr_relation_broken_not_leader(cluster_tls_context):
    """Test removing an external client relation from the charm when not leader."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    peer_relation.local_app_data["managed_users"] = (
        '{"managed_users":{"5":{"relation_id":5,"common_name":"test-common-name","ca_chain":"test_ca"}}}'
    )

    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": "new_ca",
            "common-name": "test-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": '{"ca-chain": "test_ca","common-name": "test-common-name", "keys-prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}'
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
    )

    with ctx(ctx.on.relation_broken(ecr_relation), state_in) as manager:
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users
        assert "etcd_client_relation_broken" in [event.name for event in state_out.deferred]

    # run deferred event
    with (
        ctx(ctx.on.update_status(), state_out) as manager,
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        charm: EtcdOperatorCharm = manager.charm
        # leader removed the managed user
        peer_relation.local_app_data["managed_users"] = '{"managed_users":{}}'
        state_out = manager.run()
        assert ecr_relation.id not in charm.state.cluster.managed_users
        assert "etcd_client_relation_broken" not in [event.name for event in state_out.deferred]


def test_ecr_relation_joined(cluster_tls_context):
    """Test provider adding its data to the local app data bag on ecr joining."""
    ctx, relations = cluster_tls_context

    ecr_relation = testing.Relation(
        id=len(relations) + 1,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": "new_ca",
            "common-name": "test-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
    )

    with (
        ctx(ctx.on.relation_joined(ecr_relation), state_in) as manager,
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
    ):
        manager.run()
        assert ecr_relation.local_app_data["ca-chain"] == "test_ca_server"
        assert set(ecr_relation.local_app_data["endpoints"].split(",")) == set(
            "https://ip1:2379,https://ip2:2379,https://ip0:2379".split(",")
        )


def test_non_leader_detect_ca(cluster_tls_context):
    """Test non-leader detecting the CA chain of an ecr."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    peer_relation.local_app_data["managed_users"] = (
        '{"managed_users":{"5":{"relation_id":5,"common_name":"test-common-name","ca_chain":"test_ca"}}}'
    )

    peer_relation.peers_data[4] = {
        "client_cert_ready": "True",
        "hostname": "charmed-etcd4",
        "ip": "ip4",
        "peer_cert_ready": "True",
        "tls_client_state": "tls",
        "tls_peer_state": "tls",
    }

    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "ca-chain": "new_ca",
            "common-name": "test-common-name",
            "keys-prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": '{"ca-chain": "test_ca","common-name": "test-common-name", "keys-prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}'
        },
    )

    state_in = testing.State(relations=relations + [ecr_relation], leader=False)

    # peer relation data changed
    with (
        ctx(ctx.on.relation_changed(peer_relation), state_in) as manager,
        patch(
            "events.tls.TLSEvents.collect_client_cas", return_value=["test_ca", "server_ca"]
        ) as collect_client_cas,
    ):
        manager.charm
        manager.run()
        collect_client_cas.assert_called_once()
