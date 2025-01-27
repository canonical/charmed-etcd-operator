#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from charms.tls_certificates_interface.v4.tls_certificates import (
    LIBID,
    Certificate,
    CertificateAvailableEvent,
    CertificateSigningRequest,
    PrivateKey,
    ProviderCertificate,
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)
from ops import testing
from scenario import Secret

from charm import EtcdOperatorCharm
from core.models import Member
from literals import (
    CLIENT_TLS_RELATION_NAME,
    PEER_RELATION,
    PEER_TLS_RELATION_NAME,
    RESTART_RELATION,
    TLS_PRIVATE_KEY_CONFIG,
    Status,
    TLSCARotationState,
    TLSState,
)
from managers.tls import TLSType

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


@dataclass
class CertificateAvailableContext:
    manager: testing.Manager
    peer_relation: testing.PeerRelation
    peer_tls_relation: testing.Relation
    client_tls_relation: testing.Relation
    state_in: testing.State
    provider_private_key: PrivateKey
    provider_ca_certificate: Certificate
    requirer_private_key: PrivateKey
    peer_csr: CertificateSigningRequest
    peer_certificate: Certificate
    peer_provider_certificate: ProviderCertificate
    client_csr: CertificateSigningRequest
    client_certificate: Certificate
    client_provider_certificate: ProviderCertificate


@pytest.fixture
def certificate_available_context():
    """Create a context for testing certificate available event."""
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"cluster_state": "existing"},
        local_unit_data={"ip": "localhost"},
        peers_data={
            1: {
                "ip": "localhost",
                "client_cert_ready": "True",
                "peer_cert_ready": "True",
                "tls_client_state": "tls",
                "tls_peer_state": "tls",
            },
            2: {
                "ip": "localhost",
                "client_cert_ready": "True",
                "peer_cert_ready": "True",
                "tls_client_state": "tls",
                "tls_peer_state": "tls",
            },
        },
    )
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

    state_in = testing.State(
        relations=[peer_relation, peer_tls_relation, client_tls_relation],
    )

    provider_private_key = generate_private_key()
    provider_ca_certificate = generate_ca(
        private_key=provider_private_key,
        common_name="example.com",
        validity=timedelta(days=365),
    )

    requirer_private_key = generate_private_key()
    peer_csr = generate_csr(
        private_key=requirer_private_key,
        common_name="etcd-test-1",
        organization=TLSType.PEER.value,
    )
    peer_certificate = generate_certificate(
        ca_private_key=provider_private_key,
        csr=peer_csr,
        ca=provider_ca_certificate,
        validity=timedelta(days=1),
    )
    peer_provider_certificate = ProviderCertificate(
        relation_id=peer_tls_relation.id,
        certificate=peer_certificate,
        certificate_signing_request=peer_csr,
        ca=provider_ca_certificate,
        chain=[provider_ca_certificate, peer_certificate],
        revoked=False,
    )
    client_csr = generate_csr(
        private_key=requirer_private_key,
        common_name="etcd-test-1",
        organization=TLSType.CLIENT.value,
    )
    client_certificate = generate_certificate(
        ca_private_key=provider_private_key,
        csr=client_csr,
        ca=provider_ca_certificate,
        validity=timedelta(days=1),
    )
    client_provider_certificate = ProviderCertificate(
        relation_id=client_tls_relation.id,
        certificate=client_certificate,
        certificate_signing_request=client_csr,
        ca=provider_ca_certificate,
        chain=[provider_ca_certificate, client_certificate],
        revoked=False,
    )

    return CertificateAvailableContext(
        manager=ctx(ctx.on.update_status(), state_in),
        peer_relation=peer_relation,
        peer_tls_relation=peer_tls_relation,
        client_tls_relation=client_tls_relation,
        state_in=state_in,
        provider_private_key=provider_private_key,
        provider_ca_certificate=provider_ca_certificate,
        requirer_private_key=requirer_private_key,
        peer_csr=peer_csr,
        peer_certificate=peer_certificate,
        peer_provider_certificate=peer_provider_certificate,
        client_csr=client_csr,
        client_certificate=client_certificate,
        client_provider_certificate=client_provider_certificate,
    )


def test_enable_tls_on_start():
    """Test enabling TLS on start of a new cluster."""
    ctx = testing.Context(EtcdOperatorCharm)
    with (
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("workload.EtcdWorkload.start"),
        patch("workload.EtcdWorkload.write_file"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates"
        ),
        patch("managers.tls.TLSManager.write_certificate"),
        patch("managers.cluster.ClusterManager.enable_authentication"),
    ):
        peer_relation = testing.PeerRelation(
            id=1,
            endpoint=PEER_RELATION,
            local_unit_data={
                "ip": "localhost",
            },
        )
        peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
        client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

        state_in = testing.State(
            relations=[peer_relation, peer_tls_relation, client_tls_relation], leader=True
        )
        # no tls
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == Status.ACTIVE.value.status
        assert peer_relation.local_unit_data["state"] == "started"
        assert peer_relation.local_app_data["cluster_state"] == "existing"

        # tls not ready
        peer_relation = testing.PeerRelation(
            id=1,
            endpoint=PEER_RELATION,
            local_unit_data={
                "ip": "localhost",
                "tls_peer_state": TLSState.TO_TLS.value,
            },
        )
        peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
        client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

        state_in = testing.State(
            relations=[peer_relation, peer_tls_relation, client_tls_relation], leader=True
        )
        state_out = ctx.run(ctx.on.start(), state_in)
        assert "start" in [event.name for event in state_out.deferred]

        peer_relation = testing.PeerRelation(
            id=1,
            endpoint=PEER_RELATION,
            local_unit_data={
                "ip": "localhost",
                "tls_client_state": TLSState.TO_TLS.value,
            },
        )
        peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
        client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

        state_in = testing.State(
            relations=[peer_relation, peer_tls_relation, client_tls_relation], leader=True
        )
        state_out = ctx.run(ctx.on.start(), state_in)
        assert "start" in [event.name for event in state_out.deferred]

        peer_relation = testing.PeerRelation(
            id=1,
            endpoint=PEER_RELATION,
            local_unit_data={
                "ip": "localhost",
                "tls_peer_state": TLSState.TLS.value,
                "tls_client_state": TLSState.TLS.value,
            },
        )
        peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
        client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

        state_in = testing.State(
            relations=[peer_relation, peer_tls_relation, client_tls_relation], leader=True
        )
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status == Status.ACTIVE.value.status
        assert peer_relation.local_unit_data["state"] == "started"
        assert peer_relation.local_app_data["cluster_state"] == "existing"


def test_certificates_broken():
    """Test certificates broken event."""
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={
            "client_cert_ready": "True",
            "peer_cert_ready": "True",
            "tls_client_state": "tls",
            "tls_peer_state": "tls",
        },
    )
    restart_peer_relation = testing.PeerRelation(id=4, endpoint=RESTART_RELATION)
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

    state_in = testing.State(
        relations=[peer_relation, restart_peer_relation, peer_tls_relation, client_tls_relation],
    )

    with (
        ctx(ctx.on.update_status(), state_in) as manager,
    ):
        charm: EtcdOperatorCharm = manager.charm  # type: ignore
        with (
            patch("managers.cluster.ClusterManager.broadcast_peer_url"),
            patch("managers.cluster.ClusterManager.is_healthy", return_value=True),
            patch("managers.config.ConfigManager.set_config_properties"),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.unlink"),
            patch("managers.cluster.ClusterManager.restart_member", return_value=True),
        ):
            event = MagicMock()
            event.relation.name = CLIENT_TLS_RELATION_NAME
            with patch(
                "charm.EtcdOperatorCharm.rolling_restart",
                lambda _, callback_override: charm._restart_disable_client_tls(event),
            ):
                charm.tls_events._on_certificates_broken(event)
                assert charm.state.unit_server.tls_client_state == TLSState.NO_TLS
                assert peer_relation.local_unit_data["tls_client_state"] == TLSState.NO_TLS.value
                assert peer_relation.local_unit_data["client_cert_ready"] == "False"
                assert peer_relation.local_unit_data["tls_peer_state"] == TLSState.TLS.value
                assert peer_relation.local_unit_data["peer_cert_ready"] == "True"

            event.relation.name = PEER_TLS_RELATION_NAME
            with patch(
                "charm.EtcdOperatorCharm.rolling_restart",
                lambda _, callback_override: charm._restart_disable_peer_tls(event),
            ):
                charm.tls_events._on_certificates_broken(event)
                assert charm.state.unit_server.tls_peer_state == TLSState.NO_TLS
                assert peer_relation.local_unit_data["tls_peer_state"] == TLSState.NO_TLS.value
                assert peer_relation.local_unit_data["peer_cert_ready"] == "False"
                assert peer_relation.local_unit_data["tls_client_state"] == TLSState.NO_TLS.value
                assert peer_relation.local_unit_data["client_cert_ready"] == "False"


def test_certificate_available_new_cluster(certificate_available_context):
    """Test CertificateAvailble event when creating a new cluster."""
    manager = certificate_available_context.manager
    peer_relation = certificate_available_context.peer_relation
    peer_provider_certificate = certificate_available_context.peer_provider_certificate
    requirer_private_key = certificate_available_context.requirer_private_key
    peer_certificate = certificate_available_context.peer_certificate
    provider_ca_certificate = certificate_available_context.provider_ca_certificate
    client_provider_certificate = certificate_available_context.client_provider_certificate
    client_certificate = certificate_available_context.client_certificate

    peer_relation.local_unit_data.clear()
    peer_relation.local_app_data.clear()
    with (
        manager,
        patch("pathlib.Path.read_text", return_value=provider_ca_certificate.raw),
        patch("workload.EtcdWorkload.write_file"),
        patch("pathlib.Path.exists", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        charm: EtcdOperatorCharm = manager.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)
        charm.tls_manager.set_tls_state(TLSState.TO_TLS, tls_type=TLSType.CLIENT)
        with patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([client_provider_certificate], requirer_private_key),
        ):
            event.certificate = client_certificate
            charm.tls_events._on_certificate_available(event)
            assert peer_relation.local_unit_data["tls_client_state"] == TLSState.TLS.value

        with patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([peer_provider_certificate], requirer_private_key),
        ):
            event.certificate = peer_certificate
            charm.tls_events._on_certificate_available(event)
            assert peer_relation.local_unit_data["tls_peer_state"] == TLSState.TLS.value


def test_certificate_available_enabling_tls(certificate_available_context):
    """Test CertificateAvailble event when enabling TLS."""
    manager = certificate_available_context.manager
    peer_provider_certificate = certificate_available_context.peer_provider_certificate
    requirer_private_key = certificate_available_context.requirer_private_key
    peer_certificate = certificate_available_context.peer_certificate
    provider_ca_certificate = certificate_available_context.provider_ca_certificate
    client_provider_certificate = certificate_available_context.client_provider_certificate
    client_certificate = certificate_available_context.client_certificate
    peer_relation = certificate_available_context.peer_relation
    peer_relation.local_unit_data.clear()
    peer_relation.local_app_data.clear()
    peer_relation.local_unit_data.update(
        {
            "hostname": "localhost",
            "ip": "localhost",
            "state": "started",
        }
    )
    peer_relation.local_app_data.update(
        {
            "cluster_state": "existing",
            "authenticating": "enabled",
        }
    )
    with (
        manager,
        patch("pathlib.Path.read_text", return_value=provider_ca_certificate.raw),
        patch("workload.EtcdWorkload.write_file"),
        patch("pathlib.Path.exists", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        charm: EtcdOperatorCharm = manager.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)
        charm.tls_manager.set_tls_state(TLSState.TO_TLS, tls_type=TLSType.PEER)

        with (
            patch("pathlib.Path.exists", return_value=False),
            patch("managers.cluster.ClusterManager.broadcast_peer_url"),
            patch("managers.config.ConfigManager._get_cluster_endpoints", return_value=""),
            patch("managers.cluster.ClusterManager.restart_member", return_value=True),
        ):
            # Peer cert added case
            with (
                patch(
                    "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                    return_value=([peer_provider_certificate], requirer_private_key),
                ),
                patch(
                    "charm.EtcdOperatorCharm.rolling_restart",
                    lambda _, callback: charm._restart_enable_peer_tls(event),
                ),
            ):
                event.certificate = peer_certificate
                charm.tls_events._on_certificate_available(event)
                assert charm.state.unit_server.peer_cert_ready
                assert charm.state.unit_server.tls_peer_state == TLSState.TLS

            # client cert added case
            with (
                patch(
                    "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                    return_value=([client_provider_certificate], requirer_private_key),
                ),
                patch(
                    "charm.EtcdOperatorCharm.rolling_restart",
                    lambda _, callback: charm._restart_enable_client_tls(event),
                ),
            ):
                event.certificate = client_certificate
                charm.tls_events._on_certificate_available(event)

                assert charm.state.unit_server.client_cert_ready
                assert charm.state.unit_server.tls_client_state == TLSState.TLS
                assert charm.state.unit_server.certs_ready


def test_enabling_tls_one_restart(certificate_available_context):
    """Test enabling TLS with one restart."""
    manager = certificate_available_context.manager
    peer_relation = certificate_available_context.peer_relation
    peer_provider_certificate = certificate_available_context.peer_provider_certificate
    requirer_private_key = certificate_available_context.requirer_private_key
    peer_certificate = certificate_available_context.peer_certificate
    provider_ca_certificate = certificate_available_context.provider_ca_certificate
    client_provider_certificate = certificate_available_context.client_provider_certificate
    client_certificate = certificate_available_context.client_certificate

    peer_relation.local_unit_data.clear()
    peer_relation.local_app_data.clear()
    peer_relation.local_unit_data.update(
        {
            "hostname": "localhost",
            "ip": "localhost",
            "state": "started",
        }
    )
    peer_relation.local_app_data.update(
        {
            "cluster_state": "existing",
            "authenticating": "enabled",
        }
    )

    with (
        manager,
        patch("pathlib.Path.read_text", return_value=provider_ca_certificate.raw),
        patch("workload.EtcdWorkload.write_file"),
        patch("pathlib.Path.exists", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        charm: EtcdOperatorCharm = manager.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)

        # Peer cert added case but no restart
        with (
            patch("pathlib.Path.exists", return_value=False),
            patch("common.client.EtcdClient.broadcast_peer_url"),
            patch(
                "common.client.EtcdClient.member_list",
                return_value=MEMBER_LIST_DICT,
            ),
            patch("managers.config.ConfigManager._get_cluster_endpoints", return_value=""),
            patch("managers.cluster.ClusterManager.restart_member"),
        ):
            charm.tls_manager.set_tls_state(TLSState.TO_TLS, tls_type=TLSType.PEER)
            charm.tls_manager.set_tls_state(TLSState.TO_TLS, tls_type=TLSType.CLIENT)
            with (
                patch(
                    "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                    return_value=([peer_provider_certificate], requirer_private_key),
                ),
                patch(
                    "charm.EtcdOperatorCharm.rolling_restart",
                    lambda _, callback: charm._restart_enable_peer_tls(event),
                ),
            ):
                event.certificate = peer_certificate
                charm.tls_events._on_certificate_available(event)
                assert charm.state.unit_server.peer_cert_ready
                assert charm.state.unit_server.tls_peer_state == TLSState.TO_TLS

            # client cert added case and restart handles both peer and client
            with (
                patch(
                    "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                    return_value=([client_provider_certificate], requirer_private_key),
                ),
                patch(
                    "charm.EtcdOperatorCharm.rolling_restart",
                    lambda _, callback: charm._restart_enable_client_tls(event),
                ),
            ):
                event.certificate = client_certificate
                charm.tls_events._on_certificate_available(event)

                assert charm.state.unit_server.client_cert_ready
                assert charm.state.unit_server.tls_client_state == TLSState.TLS
                assert charm.state.unit_server.tls_peer_state == TLSState.TLS
                assert charm.state.unit_server.certs_ready

        # reset databags
        peer_relation.local_unit_data.clear()
        peer_relation.local_app_data.clear()
        peer_relation.local_app_data["cluster_state"] = "existing"
        peer_relation.local_unit_data["ip"] = "localhost"
        peer_relation.local_unit_data["state"] = "started"
        # Peer cert added case but no restart
        with (
            patch("pathlib.Path.exists", return_value=False),
            patch("common.client.EtcdClient.broadcast_peer_url"),
            patch(
                "common.client.EtcdClient.member_list",
                return_value=MEMBER_LIST_DICT,
            ),
            patch("managers.config.ConfigManager._get_cluster_endpoints", return_value=""),
            patch("managers.cluster.ClusterManager.restart_member"),
        ):
            charm.tls_manager.set_tls_state(TLSState.TO_TLS, tls_type=TLSType.PEER)
            charm.tls_manager.set_tls_state(TLSState.TO_TLS, tls_type=TLSType.CLIENT)
            with (
                patch(
                    "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                    return_value=([client_provider_certificate], requirer_private_key),
                ),
                patch(
                    "charm.EtcdOperatorCharm.rolling_restart",
                    lambda _, callback: charm._restart_enable_client_tls(event),
                ),
            ):
                event.certificate = client_certificate
                charm.tls_events._on_certificate_available(event)
                assert charm.state.unit_server.client_cert_ready
                assert charm.state.unit_server.tls_client_state == TLSState.TO_TLS
                event.defer.assert_called_once()

                charm.tls_manager.set_cert_state(TLSType.PEER, True)
                charm.tls_events._on_certificate_available(event)
                assert charm.state.unit_server.tls_peer_state == TLSState.TLS
                assert charm.state.unit_server.tls_client_state == TLSState.TLS


def test_certificates_relation_created():
    """Test TLS certificates relation created."""
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)

    state_in = testing.State(
        relations=[peer_relation, peer_tls_relation],
    )

    with patch("workload.EtcdWorkload.alive", return_value=True):
        state_out = ctx.run(ctx.on.relation_created(relation=peer_tls_relation), state_in)
        assert state_out.unit_status == Status.TLS_ENABLING_PEER_TLS.value.status
        assert (
            state_out.get_relation(peer_relation.id).local_unit_data["tls_peer_state"]
            == TLSState.TO_TLS.value
        )

    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    client_tls_relation = testing.Relation(id=2, endpoint=CLIENT_TLS_RELATION_NAME)

    state_in = testing.State(
        relations=[peer_relation, client_tls_relation],
    )

    with patch("workload.EtcdWorkload.alive", return_value=True):
        state_out = ctx.run(ctx.on.relation_created(relation=client_tls_relation), state_in)
        assert state_out.unit_status == Status.TLS_ENABLING_CLIENT_TLS.value.status
        assert (
            state_out.get_relation(peer_relation.id).local_unit_data["tls_client_state"]
            == TLSState.TO_TLS.value
        )


def test_certificate_expiration(certificate_available_context):
    """Test certificate expiration."""
    manager = certificate_available_context.manager
    peer_provider_certificate = certificate_available_context.peer_provider_certificate
    requirer_private_key = certificate_available_context.requirer_private_key
    peer_certificate = certificate_available_context.peer_certificate
    provider_ca_certificate = certificate_available_context.provider_ca_certificate
    client_provider_certificate = certificate_available_context.client_provider_certificate
    client_certificate = certificate_available_context.client_certificate

    with (
        manager,
        patch("pathlib.Path.read_text", return_value=provider_ca_certificate.raw),
        patch("workload.EtcdWorkload.write_file"),
        patch("pathlib.Path.exists", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
        patch("managers.tls.TLSManager.is_new_ca", return_value=False),
    ):
        charm: EtcdOperatorCharm = manager.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)

        # Peer cert added case but no restart
        with (
            patch("pathlib.Path.exists", return_value=False),
            patch("common.client.EtcdClient.broadcast_peer_url"),
            patch(
                "common.client.EtcdClient.member_list",
                return_value=MEMBER_LIST_DICT,
            ),
            patch("managers.config.ConfigManager._get_cluster_endpoints", return_value=""),
            patch("managers.cluster.ClusterManager.restart_member"),
            patch("charm.EtcdOperatorCharm.rolling_restart") as restart_mock,
        ):
            with patch(
                "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                return_value=([peer_provider_certificate], requirer_private_key),
            ):
                charm.tls_manager.set_tls_state(TLSState.TLS, tls_type=TLSType.PEER)
                charm.tls_manager.set_tls_state(TLSState.TLS, tls_type=TLSType.CLIENT)
                event.certificate = peer_certificate
                charm.tls_events._on_certificate_available(event)
                assert charm.state.unit_server.peer_cert_ready
                assert charm.state.unit_server.tls_peer_state == TLSState.TLS
                restart_mock.assert_not_called()

            with patch(
                "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                return_value=([client_provider_certificate], requirer_private_key),
            ):
                event.certificate = client_certificate
                charm.tls_events._on_certificate_available(event)
                assert charm.state.unit_server.tls_client_state == TLSState.TLS
                restart_mock.assert_not_called()


def test_set_tls_private_key():
    """Test setting the private key through a config option."""
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={
            "client_cert_ready": "True",
            "peer_cert_ready": "True",
            "tls_client_state": "tls",
            "tls_peer_state": "tls",
            "state": "started",
        },
    )
    restart_peer_relation = testing.PeerRelation(id=4, endpoint=RESTART_RELATION)
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

    private_key = generate_private_key().raw
    secret = Secret(
        {"private-key": private_key},
        label=TLS_PRIVATE_KEY_CONFIG,
        # owner="app",
    )
    state_in = testing.State(
        relations=[peer_relation, restart_peer_relation, peer_tls_relation, client_tls_relation],
        secrets={
            secret,
            Secret(
                {"private-key": "initial_private_key"},
                label=f"{LIBID}-private-key-0",
                owner="unit",
            ),
        },
        config={"tls-private-key": secret.id},
    )

    with (
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4._cleanup_certificate_requests"
        ) as cleanup,
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4._send_certificate_requests"
        ) as send,
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert (
            state_out.get_secret(label=f"{LIBID}-private-key-0").latest_content["private-key"]  # type: ignore
            == private_key
        )
        assert cleanup.call_count == 2, (
            "_cleanup_certificate_requests should be called once for peer and once for client"
        )
        assert send.call_count == 2, (
            "_send_certificate_requests should be called once for peer and once for client"
        )

        # update private key
        newer_private_key = generate_private_key().raw
        secret.latest_content["private-key"] = base64.b64encode(  # type: ignore
            newer_private_key.encode()
        ).decode()  # type: ignore
        state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_out)

        assert (
            state_out.get_secret(label=f"{LIBID}-private-key-0").latest_content["private-key"]  # type: ignore
            == newer_private_key
        )
        assert cleanup.call_count == 4, (
            "_cleanup_certificate_requests should be called once for peer and once for client"
        )
        assert send.call_count == 4, (
            "_send_certificate_requests should be called once for peer and once for client"
        )

        # invalid key
        invalid_key = base64.b64encode("invalid_key".encode()).decode()
        secret.latest_content["private-key"] = invalid_key  # type: ignore
        state_out = ctx.run(ctx.on.secret_changed(secret=secret), state_out)
        assert (
            state_out.get_secret(label=f"{LIBID}-private-key-0").latest_content["private-key"]  # type: ignore
            == newer_private_key
        )
        assert cleanup.call_count == 4, (
            "_cleanup_certificate_requests should be called once for peer and once for client"
        )
        assert send.call_count == 4, (
            "_send_certificate_requests should be called once for peer and once for client"
        )

        state_in.config["tls-private-key"] = "secret:cu9ibpp34trs4baf20c0"

        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert (
            state_out.get_secret(label=f"{LIBID}-private-key-0").latest_content["private-key"]  # type: ignore
            == newer_private_key
        )

        assert cleanup.call_count == 4, (
            "_cleanup_certificate_requests should be called once for peer and once for client"
        )
        assert send.call_count == 4, (
            "_send_certificate_requests should be called once for peer and once for client"
        )


def test_ca_peer_rotation(certificate_available_context):
    """Test CA rotation for peer certificates."""
    manager = certificate_available_context.manager
    peer_relation = certificate_available_context.peer_relation
    peer_provider_certificate = certificate_available_context.peer_provider_certificate
    requirer_private_key = certificate_available_context.requirer_private_key
    peer_certificate = certificate_available_context.peer_certificate

    peer_relation.local_unit_data.clear()
    peer_relation.local_app_data.clear()
    peer_relation.local_unit_data.update(
        {
            "client_cert_ready": "True",
            "peer_cert_ready": "True",
            "hostname": "localhost",
            "ip": "localhost",
            "tls_client_state": "tls",
            "tls_peer_state": "tls",
            "state": "started",
        }
    )

    peer_relation.local_app_data.update(
        {
            "cluster_state": "existing",
            "authenticating": "enabled",
        }
    )
    with (
        manager,
        patch("managers.tls.TLSManager._load_trusted_ca", return_value=[]),
        patch("managers.tls.TLSManager.add_trusted_ca"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([peer_provider_certificate], requirer_private_key),
        ),
        patch("charm.EtcdOperatorCharm._restart"),
    ):
        charm: EtcdOperatorCharm = manager.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)
        event.certificate = peer_certificate

        # detect new ca and store it
        with patch(
            "charm.EtcdOperatorCharm.rolling_restart",
            lambda _, callback: charm._restart_ca_rotation(event),
        ):
            charm.tls_events._on_certificate_available(event)
            assert charm.state.unit_server.peer_cert_ready
            assert charm.state.unit_server.tls_peer_state == TLSState.TLS
            assert (
                charm.state.unit_server.tls_peer_ca_rotation_state
                == TLSCARotationState.NEW_CA_ADDED
            )
            event.defer.assert_called_once()

        # Other units have not updated their certs
        charm.tls_events._on_certificate_available(event)
        assert (
            charm.state.unit_server.tls_peer_ca_rotation_state == TLSCARotationState.NEW_CA_ADDED
        ), "Peer CA rotation state should not be updated"
        assert event.defer.call_count == 2, "Should defer until all units have updated their certs"

        peer_relation.peers_data[1]["tls_peer_ca_rotation"] = TLSCARotationState.NEW_CA_ADDED.value
        peer_relation.peers_data[2]["tls_peer_ca_rotation"] = TLSCARotationState.NEW_CA_ADDED.value
        with (
            patch("managers.tls.TLSManager.write_certificate") as write_certificate_mock,
            patch("charm.EtcdOperatorCharm.rolling_restart") as restart_mock,
        ):
            charm.tls_events._on_certificate_available(event)
            assert (
                charm.state.unit_server.tls_peer_ca_rotation_state
                == TLSCARotationState.CERT_UPDATED
            )
            write_certificate_mock.assert_called_once()
            restart_mock.assert_not_called()
            assert event.defer.call_count == 2, "event should not have been deferred"

        with (
            patch(
                "charm.EtcdOperatorCharm.rolling_restart",
                lambda _, callback: charm._restart_clean_cas(None),
            ),
            patch("managers.tls.TLSManager._load_trusted_ca", return_value=["old_ca", "new_ca"]),
            patch("workload.EtcdWorkload.remove_file"),
            patch("managers.tls.TLSManager.add_trusted_ca"),
        ):
            # clean up old cas
            event = MagicMock()
            event.cert_type = TLSType.PEER
            # Not all units have added the new ca
            peer_relation.peers_data[1]["tls_peer_ca_rotation"] = (
                TLSCARotationState.NEW_CA_DETECTED.value
            )
            charm.tls_events._on_clean_ca(event)
            event.defer.assert_called_once()

            # all units have updated their certs
            peer_relation.peers_data[1]["tls_peer_ca_rotation"] = (
                TLSCARotationState.CERT_UPDATED.value
            )
            peer_relation.peers_data[2]["tls_peer_ca_rotation"] = (
                TLSCARotationState.CERT_UPDATED.value
            )

            manager.run()
            assert (
                peer_relation.local_unit_data["tls_peer_ca_rotation"]
                == TLSCARotationState.NO_ROTATION.value
            )
            event.defer.assert_called_once()


def test_ca_client_rotation(certificate_available_context):
    """Test CA rotation for client certificates."""
    manager = certificate_available_context.manager
    peer_relation = certificate_available_context.peer_relation
    client_provider_certificate = certificate_available_context.client_provider_certificate
    requirer_private_key = certificate_available_context.requirer_private_key
    client_certificate = certificate_available_context.client_certificate

    peer_relation.local_unit_data.clear()
    peer_relation.local_app_data.clear()
    peer_relation.local_unit_data.update(
        {
            "client_cert_ready": "True",
            "peer_cert_ready": "True",
            "hostname": "localhost",
            "ip": "localhost",
            "tls_client_state": "tls",
            "tls_peer_state": "tls",
            "state": "started",
        }
    )

    peer_relation.local_app_data.update(
        {
            "cluster_state": "existing",
            "authenticating": "enabled",
        }
    )
    with (
        manager,
        patch("managers.tls.TLSManager._load_trusted_ca", return_value=[]),
        patch("managers.tls.TLSManager.add_trusted_ca"),
        patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([client_provider_certificate], requirer_private_key),
        ),
        patch("charm.EtcdOperatorCharm._restart"),
    ):
        charm: EtcdOperatorCharm = manager.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)
        event.certificate = client_certificate

        # detect new ca and store it
        with patch(
            "charm.EtcdOperatorCharm.rolling_restart",
            lambda _, callback: charm._restart_ca_rotation(event),
        ):
            charm.tls_events._on_certificate_available(event)
            assert charm.state.unit_server.client_cert_ready
            assert charm.state.unit_server.tls_client_state == TLSState.TLS
            assert (
                charm.state.unit_server.tls_client_ca_rotation_state
                == TLSCARotationState.NEW_CA_ADDED
            )
            event.defer.assert_called_once()

        # Other units have not updated their certs
        charm.tls_events._on_certificate_available(event)
        assert (
            charm.state.unit_server.tls_client_ca_rotation_state == TLSCARotationState.NEW_CA_ADDED
        ), "Client CA rotation state should not be updated"
        assert event.defer.call_count == 2, "Should defer until all units have updated their certs"

        peer_relation.peers_data[1]["tls_client_ca_rotation"] = (
            TLSCARotationState.NEW_CA_ADDED.value
        )
        peer_relation.peers_data[2]["tls_client_ca_rotation"] = (
            TLSCARotationState.NEW_CA_ADDED.value
        )
        with (
            patch("managers.tls.TLSManager.write_certificate") as write_certificate_mock,
            patch("charm.EtcdOperatorCharm.rolling_restart") as restart_mock,
        ):
            charm.tls_events._on_certificate_available(event)
            assert (
                charm.state.unit_server.tls_client_ca_rotation_state
                == TLSCARotationState.CERT_UPDATED
            )
            write_certificate_mock.assert_called_once()
            restart_mock.assert_not_called()
            assert event.defer.call_count == 2, "event should not have been deferred"

        with (
            patch(
                "charm.EtcdOperatorCharm.rolling_restart",
                lambda _, callback: charm._restart_clean_cas(None),
            ),
            patch("managers.tls.TLSManager._load_trusted_ca", return_value=["old_ca", "new_ca"]),
            patch("workload.EtcdWorkload.remove_file"),
            patch("managers.tls.TLSManager.add_trusted_ca"),
        ):
            # clean up old cas
            event = MagicMock()
            event.cert_type = TLSType.CLIENT
            # Not all units have added the new ca
            peer_relation.peers_data[1]["tls_client_ca_rotation"] = (
                TLSCARotationState.NEW_CA_DETECTED.value
            )
            charm.tls_events._on_clean_ca(event)
            event.defer.assert_called_once()

            # all units have updated their certs
            peer_relation.peers_data[1]["tls_client_ca_rotation"] = (
                TLSCARotationState.CERT_UPDATED.value
            )
            peer_relation.peers_data[2]["tls_client_ca_rotation"] = (
                TLSCARotationState.CERT_UPDATED.value
            )

            manager.run()
            assert (
                peer_relation.local_unit_data["tls_client_ca_rotation"]
                == TLSCARotationState.NO_ROTATION.value
            )
            event.defer.assert_called_once()
