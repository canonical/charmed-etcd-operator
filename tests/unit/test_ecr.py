#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)
from ops import testing
from scenario import Secret, State

from charm import EtcdOperatorCharm
from literals import EXTERNAL_CLIENTS_RELATION, TLSCARotationState, TLSType

CLIENT_COMMON_NAME = "test-common-name"
server_cert = MagicMock()
server_cert.ca.raw = "test_ca_server"


@pytest.fixture
def mtls_chain():
    ca_private_key = generate_private_key()
    ca_cert = generate_ca(
        private_key=ca_private_key, validity=timedelta(days=365), common_name="ca_common_name"
    )

    client_private_key = generate_private_key()
    client_csr = generate_csr(private_key=client_private_key, common_name=CLIENT_COMMON_NAME)
    client_cert = generate_certificate(
        client_csr, ca_cert, ca_private_key, validity=timedelta(days=365)
    )
    return "\n".join([client_cert.raw, ca_cert.raw])


@pytest.fixture
def mtls_chain_same_common_name():
    ca_private_key = generate_private_key()
    ca_cert = generate_ca(
        private_key=ca_private_key, validity=timedelta(days=365), common_name="ca_common_name"
    )

    client_private_key = generate_private_key()
    client_csr = generate_csr(private_key=client_private_key, common_name=CLIENT_COMMON_NAME)
    client_cert = generate_certificate(
        client_csr, ca_cert, ca_private_key, validity=timedelta(days=365)
    )
    return "\n".join([client_cert.raw, ca_cert.raw])


@pytest.fixture
def mtls_chain_diff_common_name():
    ca_private_key = generate_private_key()
    ca_cert = generate_ca(
        private_key=ca_private_key, validity=timedelta(days=365), common_name="ca_common_name"
    )

    client_private_key = generate_private_key()
    client_csr = generate_csr(
        private_key=client_private_key, common_name=f"diff-{CLIENT_COMMON_NAME}"
    )
    client_cert = generate_certificate(
        client_csr, ca_cert, ca_private_key, validity=timedelta(days=365)
    )
    return "\n".join([client_cert.raw, ca_cert.raw])


def _get_secret_from_state(state: State, secret_id: str) -> Secret:
    for secret in state.secrets:
        if secret.id == secret_id:
            return secret
    raise ValueError(f"Secret with id {secret_id} not found in state")


def test_add_ecr_new_user_leader(cluster_tls_context, mtls_chain):
    """Test adding an external client relation to the charm."""
    ctx, relations = cluster_tls_context
    secret = Secret({"mtls-chain": mtls_chain}, owner="app")
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris", "mtls-chain"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value=None),
        patch("common.client.EtcdClient._run_etcdctl", return_value="success"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
        patch("workload.EtcdWorkload.write_file"),
        patch("managers.tls.TLSManager.is_new_ca", return_value=True) as is_new_ca,
        patch("managers.cluster.ClusterManager.restart_member") as restart_member,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users
        assert charm.state.cluster.managed_users[ecr_relation.id] == CLIENT_COMMON_NAME
        assert (
            _get_secret_from_state(
                state_out, ecr_relation.local_app_data["secret-tls"]
            ).tracked_content.get("tls-ca")
            == "test_ca_server"
        )
        assert set(ecr_relation.local_app_data["endpoints"].split(",")) == set(
            "https://ip1:2379,https://ip2:2379,https://ip0:2379".split(",")
        )
        assert (
            _get_secret_from_state(
                state_out, ecr_relation.local_app_data["secret-user"]
            ).tracked_content.get("username")
            == CLIENT_COMMON_NAME
        )
        is_new_ca.assert_called_once()
        restart_member.assert_called_once()


def test_add_ecr_new_user_not_leader(cluster_tls_context, mtls_chain):
    """Test adding an external client relation to the charm."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]

    secret = Secret(
        {"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=False,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("managers.cluster.ClusterManager.add_managed_user") as add_managed_user,
    ):
        state_out = manager.run()
        add_managed_user.assert_not_called()
        assert "mtls_chain_updated" in [event.name for event in state_out.deferred]

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("managers.cluster.ClusterManager.add_managed_user") as add_managed_user,
    ):
        peer_relation.local_app_data["managed_users"] = f'{{"5":"{CLIENT_COMMON_NAME}"}}'
        state_out = manager.run()
        add_managed_user.assert_not_called()
        assert "mtls_chain_updated" not in [event.name for event in state_out.deferred]
        # TODO add more assertions after checking custom event not being emitted in non leader setting
        # restart_member.assert_called_once()


def test_add_ecr_new_user_no_tls_leader(cluster_no_tls_context, mtls_chain):
    """Test adding an external client relation to the charm before TLS is enabled."""
    ctx, relations = cluster_no_tls_context

    secret = Secret(
        {"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        defered_event_names = [event.name for event in state_out.deferred]
        assert "mtls_chain_updated" in defered_event_names
        assert ecr_relation.id not in charm.state.cluster.managed_users


def test_add_ecr_new_user_no_tls_not_leader(cluster_no_tls_context):
    """Test adding an external client relation to the charm before TLS is enabled."""
    ctx, relations = cluster_no_tls_context

    secret = Secret(
        {"mtls-chain": "test_ca"},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        defered_event_names = [event.name for event in state_out.deferred]
        assert "mtls_chain_updated" in defered_event_names
        assert ecr_relation.id not in charm.state.cluster.managed_users


def test_add_ecr_new_user_incomplete_data_from_requirer(cluster_no_tls_context, mtls_chain):
    """Test adding an external client relation to the charm with missing data from requirer."""
    ctx, relations = cluster_no_tls_context

    secret = Secret(
        {"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            # "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        defered_event_names = [event.name for event in state_out.deferred]
        assert "mtls_chain_updated" in defered_event_names
        assert ecr_relation.id not in charm.state.cluster.managed_users


def test_add_ecr_existing_user_in_leader(cluster_tls_context, mtls_chain):
    """Test adding an external client relation to the charm with the user already existing."""
    ctx, relations = cluster_tls_context

    secret = Secret(
        {"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value={"name": CLIENT_COMMON_NAME}),
        patch("managers.tls.TLSManager.update_cas") as update_cas,
    ):
        charm: EtcdOperatorCharm = manager.charm
        manager.run()
        assert ecr_relation.id not in charm.state.cluster.managed_users
        update_cas.assert_not_called()

    state_in = testing.State(relations=relations + [ecr_relation], leader=False, secrets=[secret])

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value={"name": CLIENT_COMMON_NAME}),
        patch("managers.tls.TLSManager.is_new_ca") as is_new_ca,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id not in charm.state.cluster.managed_users
        assert "mtls_chain_updated" in [event.name for event in state_out.deferred]
        is_new_ca.assert_not_called()


def test_add_ecr_existing_user_in_non_leader(cluster_tls_context, mtls_chain):
    """Test adding an external client relation to the charm with the user already existing."""
    ctx, relations = cluster_tls_context

    secret = Secret(
        {"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=len(relations) + 1,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
    )

    state_in = testing.State(relations=relations + [ecr_relation], leader=False, secrets=[secret])

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("managers.tls.TLSManager.is_new_ca") as is_new_ca,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id not in charm.state.cluster.managed_users
        assert "mtls_chain_updated" in [event.name for event in state_out.deferred]
        is_new_ca.assert_not_called()


def test_ecr_update_common_name_leader(
    cluster_tls_context, mtls_chain, mtls_chain_diff_common_name
):
    """Test updating the common name for an external client relation."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    old_common_name = CLIENT_COMMON_NAME
    peer_relation.local_app_data["managed_users"] = f'{{"5":"{old_common_name}"}}'

    secret = Secret(
        tracked_content={"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": f'{{"secret-mtls": "{secret.id}","prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"uris\\", \\"tls\\", \\"tls-ca\\", \\"mtls-chain\\"]"}}'
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        patch("common.client.EtcdClient.get_user", return_value=None),
        patch("common.client.EtcdClient._run_etcdctl", return_value="success"),
        patch("workload.EtcdWorkload.write_file"),
        patch("events.tls.TLSEvents.collect_client_cas", return_value=["test_ca", "test_ca1"]),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
        patch("managers.cluster.ClusterManager.restart_member"),
        patch("managers.cluster.ClusterManager.remove_role") as remove_role,
        patch("managers.cluster.ClusterManager.remove_user") as remove_user,
    ):
        with (
            ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        ):
            manager.run()
            remove_role.assert_not_called()
            remove_user.assert_not_called()

        secret = Secret(
            tracked_content={"mtls-chain": mtls_chain},
            latest_content={"mtls-chain": mtls_chain_diff_common_name},
            label=secret.label,
        )
        ecr_relation = testing.Relation(
            id=5,
            endpoint=EXTERNAL_CLIENTS_RELATION,
            remote_app_data={
                "secret-mtls": secret.id,
                "prefix": "/test/keys",
                "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
            },
            local_app_data={
                "data": f'{{"secret-mtls": "{secret.id}","prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"uris\\", \\"tls\\", \\"tls-ca\\", \\"mtls-chain\\"]"}}'
            },
        )
        state_in = testing.State(
            relations=relations + [ecr_relation],
            leader=True,
            secrets=[secret],
        )

        with (
            ctx(ctx.on.secret_changed(secret), state_in) as manager,
        ):
            charm: EtcdOperatorCharm = manager.charm
            manager.run()
            assert ecr_relation.id in charm.state.cluster.managed_users
            assert (
                charm.state.cluster.managed_users[ecr_relation.id] == f"diff-{CLIENT_COMMON_NAME}"
            )
            remove_role.assert_called_once_with(old_common_name)
            remove_user.assert_called_once_with(old_common_name)


def test_ecr_update_chain_same_common_name(
    cluster_tls_context, mtls_chain, mtls_chain_same_common_name
):
    """Test updating the common name for an external client relation."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    old_common_name = CLIENT_COMMON_NAME
    peer_relation.local_app_data["managed_users"] = f'{{"5":"{old_common_name}"}}'

    secret = Secret(
        tracked_content={"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": f'{{"secret-mtls": "{secret.id}","prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"uris\\", \\"tls\\", \\"tls-ca\\", \\"mtls-chain\\"]"}}'
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        patch("common.client.EtcdClient.get_user", return_value=None),
        patch("common.client.EtcdClient._run_etcdctl", return_value="success"),
        patch("workload.EtcdWorkload.write_file"),
        patch("events.tls.TLSEvents.collect_client_cas", return_value=["test_ca", "test_ca1"]),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
        patch("managers.cluster.ClusterManager.restart_member"),
        patch("managers.cluster.ClusterManager.remove_role") as remove_role,
        patch("managers.cluster.ClusterManager.remove_user") as remove_user,
    ):
        with (
            ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        ):
            manager.run()
            remove_role.assert_not_called()
            remove_user.assert_not_called()

        secret = Secret(
            tracked_content={"mtls-chain": mtls_chain},
            latest_content={"mtls-chain": mtls_chain_same_common_name},
            label=secret.label,
        )
        ecr_relation = testing.Relation(
            id=5,
            endpoint=EXTERNAL_CLIENTS_RELATION,
            remote_app_data={
                "secret-mtls": secret.id,
                "prefix": "/test/keys",
                "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
            },
            local_app_data={
                "data": f'{{"secret-mtls": "{secret.id}","prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"uris\\", \\"tls\\", \\"tls-ca\\", \\"mtls-chain\\"]"}}'
            },
        )
        state_in = testing.State(
            relations=relations + [ecr_relation],
            leader=True,
            secrets=[secret],
        )

        with (
            ctx(ctx.on.secret_changed(secret), state_in) as manager,
            patch("managers.tls.TLSManager.is_new_ca", return_value=True) as is_new_ca,
        ):
            charm: EtcdOperatorCharm = manager.charm
            manager.run()
            assert ecr_relation.id in charm.state.cluster.managed_users
            assert charm.state.cluster.managed_users[ecr_relation.id] == CLIENT_COMMON_NAME
            remove_role.assert_not_called()
            remove_user.assert_not_called()
            is_new_ca.assert_called_once()


def test_ecr_update_common_name_non_leader(
    cluster_tls_context, mtls_chain, mtls_chain_diff_common_name
):
    """Test updating the common name for an external client relation."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    old_common_name = CLIENT_COMMON_NAME
    peer_relation.local_app_data["managed_users"] = f'{{"5":"{old_common_name}"}}'

    secret = Secret(
        {"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": f'{{"secret-mtls": "{secret.id}","common-name": "{old_common_name}", "prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}}'
        },
    )

    state_in = testing.State(relations=relations + [ecr_relation], leader=False, secrets=[secret])

    with ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager:
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users

    secret = Secret(
        tracked_content={"mtls-chain": mtls_chain},
        latest_content={"mtls-chain": mtls_chain_diff_common_name},
        label=secret.label,
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": f'{{"secret-mtls": "{secret.id}","common-name": "{old_common_name}", "prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}}'
        },
    )
    state_in = testing.State(relations=relations + [ecr_relation], leader=False, secrets=[secret])
    with (
        ctx(ctx.on.secret_changed(secret), state_in) as manager,
        patch("managers.tls.TLSManager.is_new_ca", return_value=False) as is_new_ca,
    ):
        peer_relation.local_app_data["managed_users"] = f'{{"5":"diff-{CLIENT_COMMON_NAME}"}}'
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users
        is_new_ca.assert_called_once()
        assert "mtls_chain_updated" not in [event.name for event in state_out.deferred]


def test_ecr_update_ca_chain_while_rotation_happening(cluster_tls_context):
    """Test updating the CA chain for an external client relation while rotation is happening."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    peer_relation.local_app_data["managed_users"] = f'{{"5":"{CLIENT_COMMON_NAME}"}}'

    peer_relation.local_unit_data["tls_client_ca_rotation"] = TLSCARotationState.NEW_CA_ADDED.value

    secret = Secret(
        {"mtls-chain": "test_ca"},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": f'{{"secret-mtls": "{secret.id}", "prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}}'
        },
    )

    state_in = testing.State(relations=relations + [ecr_relation], leader=True, secrets=[secret])

    # register secret label
    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
    ):
        state_out = manager.run()

    with (
        ctx(ctx.on.secret_changed(secret), state_out) as manager,
    ):
        state_out = manager.run()
        assert "mtls_chain_updated" in [event.name for event in state_out.deferred]


def test_ecr_relation_broken_leader(cluster_tls_context, mtls_chain):
    """Test removing an external client relation from the charm."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    peer_relation.local_app_data["managed_users"] = f'{{"5":"{CLIENT_COMMON_NAME}"}}'

    secret = Secret(
        {"mtls-chain": mtls_chain},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": '{"ca-chain": "test_ca", "prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}'
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
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
        remove_role.assert_called_once_with(CLIENT_COMMON_NAME)
        remove_user.assert_called_once_with(CLIENT_COMMON_NAME)
        collect_client_cas.assert_called_once()
        update_cas.assert_called_once()
        restart.assert_called_once()


def test_ecr_relation_broken_not_leader(cluster_tls_context):
    """Test removing an external client relation from the charm when not leader."""
    ctx, relations = cluster_tls_context

    peer_relation = relations[0]
    peer_relation.local_app_data["managed_users"] = f'{{"5":"{CLIENT_COMMON_NAME}"}}'

    secret = Secret(
        {"mtls-chain": "test_ca"},
    )
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris"]',
        },
        local_app_data={
            "data": '{"ca-chain": "test_ca", "prefix":"/test/", "requested-secrets": "[\\"username\\",\\"password\\", \\"tls\\", \\"tls-ca\\", \\"uris\\"]"}'
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=False,
        secrets=[secret],
    )
    with (
        ctx(ctx.on.relation_broken(ecr_relation), state_in) as manager,
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.cluster.ClusterManager.remove_managed_user") as remove_managed_user,
    ):
        manager.run()
        remove_managed_user.assert_not_called()
        # TODO add more assertions after checking custom event not being emitted in non leader setting


def test_etcd_rotates_ca(cluster_tls_context, mtls_chain):
    """Test rotating the CA chain of etcd for an external client relation."""
    ctx, relations = cluster_tls_context
    secret = Secret({"mtls-chain": mtls_chain}, owner="app")
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris", "mtls-chain"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value=None),
        patch("common.client.EtcdClient._run_etcdctl", return_value="success"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
        patch("workload.EtcdWorkload.write_file"),
        patch("managers.tls.TLSManager.is_new_ca", return_value=True) as is_new_ca,
        patch("managers.cluster.ClusterManager.restart_member") as restart_member,
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users
        assert charm.state.cluster.managed_users[ecr_relation.id] == CLIENT_COMMON_NAME
        assert (
            _get_secret_from_state(
                state_out, ecr_relation.local_app_data["secret-tls"]
            ).tracked_content.get("tls-ca")
            == "test_ca_server"
        )

        new_server_cert = MagicMock()
        new_server_cert.ca.raw = "new_test_ca_server"

        with (
            ctx(ctx.on.update_status(), state_out) as manager,
            patch(
                "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                return_value=([new_server_cert], MagicMock()),
            ),
            patch("managers.tls.TLSManager.is_new_ca", return_value=False),
            patch("managers.tls.TLSManager.is_new_ca_saved_on_all_servers", return_value=True),
            patch("managers.tls.TLSManager.write_certificate"),
            patch("managers.tls.TLSManager.update_cas"),
            patch("events.tls.TLSEvents.collect_client_cas", return_value=["test_ca", "test_ca1"]),
        ):
            charm: EtcdOperatorCharm = manager.charm
            event = MagicMock(spec=CertificateAvailableEvent)
            cert = MagicMock()
            cert.organization = TLSType.CLIENT
            event.certificate = cert
            charm.tls_manager.set_ca_rotation_state(
                TLSType.CLIENT, TLSCARotationState.NEW_CA_ADDED
            )
            charm.tls_events._on_certificate_available(event)
            secret_tls = _get_secret_from_state(
                state_out, ecr_relation.local_app_data["secret-tls"]
            )
            assert secret_tls.latest_content
            assert secret_tls.latest_content.get("tls-ca") == "new_test_ca_server"


def test_etcd_updates_endpoints(cluster_tls_context, mtls_chain):
    """Test updating the endpoints of etcd for an external client relation."""
    ctx, relations = cluster_tls_context
    secret = Secret({"mtls-chain": mtls_chain}, owner="app")
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris", "mtls-chain"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value=None),
        patch("common.client.EtcdClient._run_etcdctl", return_value="success"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
        patch("workload.EtcdWorkload.write_file"),
        patch("managers.tls.TLSManager.is_new_ca", return_value=True),
        patch("managers.cluster.ClusterManager.restart_member"),
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users
        assert charm.state.cluster.managed_users[ecr_relation.id] == CLIENT_COMMON_NAME
        assert set(ecr_relation.local_app_data["endpoints"].split(",")) == set(
            "https://ip1:2379,https://ip2:2379,https://ip0:2379".split(",")
        )

    peer_relation = relations[0]
    peer_relation.local_unit_data["ip"] = "ip10"
    with (
        ctx(ctx.on.relation_changed(peer_relation), state_out) as manager,
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert set(ecr_relation.local_app_data["endpoints"].split(",")) == set(
            "https://ip10:2379,https://ip2:2379,https://ip1:2379".split(",")
        )


def test_etcd_updates_version(cluster_tls_context, mtls_chain):
    """Test updating the version of etcd for an external client relation."""
    ctx, relations = cluster_tls_context
    secret = Secret({"mtls-chain": mtls_chain}, owner="app")
    ecr_relation = testing.Relation(
        id=5,
        endpoint=EXTERNAL_CLIENTS_RELATION,
        remote_app_data={
            "secret-mtls": secret.id,
            "prefix": "/test/keys",
            "requested-secrets": '["username", "password", "tls", "tls-ca", "uris", "mtls-chain"]',
        },
    )

    state_in = testing.State(
        relations=relations + [ecr_relation],
        leader=True,
        secrets=[secret],
    )

    with (
        ctx(ctx.on.relation_changed(ecr_relation), state_in) as manager,
        patch("common.client.EtcdClient.get_user", return_value=None),
        patch("common.client.EtcdClient._run_etcdctl", return_value="success"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5"),
        patch("workload.EtcdWorkload.write_file"),
        patch("managers.tls.TLSManager.is_new_ca", return_value=True),
        patch("managers.cluster.ClusterManager.restart_member"),
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.id in charm.state.cluster.managed_users
        assert charm.state.cluster.managed_users[ecr_relation.id] == CLIENT_COMMON_NAME
        assert ecr_relation.local_app_data["version"] == "3.5"

    peer_relation = relations[0]
    with (
        ctx(ctx.on.relation_changed(peer_relation), state_out) as manager,
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([server_cert], MagicMock()),
        ),
        patch("managers.cluster.ClusterManager.get_version", return_value="3.5.1"),
    ):
        charm: EtcdOperatorCharm = manager.charm
        state_out = manager.run()
        assert ecr_relation.local_app_data["version"] == "3.5.1"


def test_update_client_relations_data_non_leader(cluster_tls_context):
    """Test updating the data of an external client relation when not leader."""
    ctx, relations = cluster_tls_context

    state_in = testing.State(relations=relations, leader=False)
    with (
        ctx(ctx.on.relation_changed(relations[0]), state_in) as manager,
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
        ) as get_assigned_certificates,
    ):
        charm: EtcdOperatorCharm = manager.charm
        charm.external_clients_events.update_client_relations_data()
        get_assigned_certificates.assert_not_called()


def test_update_client_relations_data_no_external_clients(cluster_tls_context):
    """Test updating the data of an external client relation when not leader."""
    ctx, relations = cluster_tls_context

    state_in = testing.State(relations=relations, leader=True)
    with (
        ctx(ctx.on.relation_changed(relations[0]), state_in) as manager,
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
        ) as get_assigned_certificates,
    ):
        manager.run()
        get_assigned_certificates.assert_not_called()
