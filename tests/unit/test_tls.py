#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    ProviderCertificate,
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)
from ops import testing

from charm import EtcdOperatorCharm
from literals import (
    CLIENT_TLS_RELATION_NAME,
    PEER_RELATION,
    PEER_TLS_RELATION_NAME,
    RESTART_RELATION,
    Status,
    TLSState,
)
from managers.tls import CertType


def test_enable_tls_on_start():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

    state_in = testing.State(
        relations=[peer_relation, peer_tls_relation],
    )

    with patch("workload.EtcdWorkload.alive", return_value=True):
        state_out = ctx.run(ctx.on.relation_joined(relation=peer_tls_relation), state_in)
        assert state_out.unit_status == Status.CLIENT_TLS_MISSING.value.status

    state_in = testing.State(
        relations=[peer_relation, client_tls_relation],
    )
    with patch("workload.EtcdWorkload.alive", return_value=True):
        state_out = ctx.run(ctx.on.relation_joined(relation=client_tls_relation), state_in)
        assert state_out.unit_status == Status.PEER_TLS_MISSING.value.status


def test_certificates_broken():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={
            "client-cert-ready": "True",
            "peer-cert-ready": "True",
            "tls-state": "tls",
        },
    )
    restart_peer_relation = testing.PeerRelation(id=4, endpoint=RESTART_RELATION)
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

    state_in = testing.State(
        relations=[peer_relation, restart_peer_relation, peer_tls_relation, client_tls_relation],
    )

    state_out = ctx.run(ctx.on.relation_broken(relation=peer_tls_relation), state_in)
    assert state_out.unit_status == Status.CLIENT_TLS_NEEDS_TO_BE_REMOVED.value.status
    assert state_out.get_relation(peer_relation.id).local_unit_data["tls-state"] == "to-no-tls"
    assert state_out.get_relation(peer_relation.id).local_unit_data["peer-cert-ready"] == "False"
    assert state_out.get_relation(peer_relation.id).local_unit_data["client-cert-ready"] == "True"

    peer_relation.local_unit_data["peer-cert-ready"] = "True"
    state_out = ctx.run(ctx.on.relation_broken(relation=client_tls_relation), state_in)
    assert state_out.unit_status == Status.PEER_TLS_NEEDS_TO_BE_REMOVED.value.status
    assert state_out.get_relation(peer_relation.id).local_unit_data["tls-state"] == "to-no-tls"
    assert state_out.get_relation(peer_relation.id).local_unit_data["client-cert-ready"] == "False"
    assert state_out.get_relation(peer_relation.id).local_unit_data["peer-cert-ready"] == "True"

    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_unit_data={
            "client-cert-ready": "True",
            "peer-cert-ready": "True",
            "tls-state": "tls",
        },
    )
    restart_peer_relation = testing.PeerRelation(id=4, endpoint=RESTART_RELATION)
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

    state_in = testing.State(
        relations=[peer_relation, restart_peer_relation, peer_tls_relation, client_tls_relation],
    )
    ctx.run(ctx.on.relation_broken(relation=peer_tls_relation), state_in)

    state_in = testing.State(
        relations=[peer_relation, restart_peer_relation, client_tls_relation],
    )

    with (
        ctx(ctx.on.update_status(), state_in) as mgr,
    ):
        charm: EtcdOperatorCharm = mgr.charm  # type: ignore

        def mock_rolling_restart(_):
            charm._restart(None)

        with (
            patch("charm.EtcdOperatorCharm.rolling_restart", mock_rolling_restart),
            patch("managers.cluster.ClusterManager.broadcast_peer_url"),
            patch("managers.cluster.ClusterManager.health_check", return_value=True),
            patch("managers.config.ConfigManager.set_config_properties"),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.unlink"),
        ):
            event = MagicMock()
            event.relation.name = CLIENT_TLS_RELATION_NAME
            charm.tls_events._on_certificates_broken(event)
            assert charm.state.unit_server.tls_state == TLSState.NO_TLS


def test_certificate_available_new_cluster():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(id=1, endpoint=PEER_RELATION)
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

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
        organization=CertType.PEER.value,
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
        organization=CertType.CLIENT.value,
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

    state_in = testing.State(
        relations=[peer_relation, client_tls_relation],
    )
    with (
        ctx(ctx.on.update_status(), state_in) as mgr,
        patch("pathlib.Path.read_text", return_value=provider_ca_certificate.raw),
        patch("workload.EtcdWorkload.write_file"),
        patch("pathlib.Path.exists", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        charm: EtcdOperatorCharm = mgr.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)
        charm.tls_manager.set_tls_state(TLSState.TO_TLS)
        with patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([client_provider_certificate], requirer_private_key),
        ):
            event.certificate = client_certificate
            charm.tls_events._on_certificate_available(event)
        assert charm.unit.status == Status.PEER_TLS_MISSING.value.status

    peer_relation.local_unit_data.clear()
    peer_relation.local_app_data.clear()
    state_in = testing.State(
        relations=[peer_relation, peer_tls_relation],
    )
    with (
        ctx(ctx.on.update_status(), state_in) as mgr,
        patch("pathlib.Path.read_text", return_value=provider_ca_certificate.raw),
        patch("workload.EtcdWorkload.write_file"),
        patch("pathlib.Path.exists", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        charm: EtcdOperatorCharm = mgr.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)
        charm.tls_manager.set_tls_state(TLSState.TO_TLS)
        with patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([peer_provider_certificate], requirer_private_key),
        ):
            event.certificate = client_certificate
            charm.tls_events._on_certificate_available(event)
        assert charm.unit.status == Status.CLIENT_TLS_MISSING.value.status

    peer_relation.local_unit_data.clear()
    peer_relation.local_app_data.clear()
    state_in = testing.State(
        relations=[peer_relation, peer_tls_relation, client_tls_relation],
    )
    with (
        ctx(ctx.on.update_status(), state_in) as mgr,
        patch("pathlib.Path.read_text", return_value=provider_ca_certificate.raw),
        patch("workload.EtcdWorkload.write_file"),
        patch("pathlib.Path.exists", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        charm: EtcdOperatorCharm = mgr.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)
        charm.tls_manager.set_tls_state(TLSState.TO_TLS)

        with (
            patch(
                "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                return_value=([peer_provider_certificate], None),
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            event.certificate = peer_certificate
            with pytest.raises(Exception):
                charm.tls_events._on_certificate_available(event)

        with (
            patch(
                "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                return_value=([peer_provider_certificate], requirer_private_key),
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            event.certificate = peer_certificate
            charm.tls_events._on_certificate_available(event)
            assert charm.unit.status == Status.TLS_NOT_READY.value.status
            assert charm.state.unit_server.peer_cert_ready

        with patch(
            "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
            return_value=([client_provider_certificate], requirer_private_key),
        ):
            event.certificate = client_certificate
            charm.tls_events._on_certificate_available(event)

            assert charm.state.unit_server.client_cert_ready
            assert charm.state.unit_server.certs_ready
            assert charm.state.unit_server.tls_state == TLSState.TLS


def test_certificate_available_turning_tls_on():
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={"initial-cluster-state": "existing"},
        local_unit_data={"ip": "localhost"},
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
        organization=CertType.PEER.value,
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
        organization=CertType.CLIENT.value,
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

    with (
        ctx(ctx.on.update_status(), state_in) as mgr,
        patch("pathlib.Path.read_text", return_value=provider_ca_certificate.raw),
        patch("workload.EtcdWorkload.write_file"),
        patch("pathlib.Path.exists", return_value=True),
        patch("workload.EtcdWorkload.alive", return_value=True),
    ):
        charm: EtcdOperatorCharm = mgr.charm  # type: ignore
        event = MagicMock(spec=CertificateAvailableEvent)
        charm.tls_manager.set_tls_state(TLSState.TO_TLS)

        with (
            patch(
                "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                return_value=([peer_provider_certificate], requirer_private_key),
            ),
            patch("pathlib.Path.exists", return_value=False),
        ):
            event.certificate = peer_certificate
            charm.tls_events._on_certificate_available(event)
            assert charm.unit.status == Status.TLS_NOT_READY.value.status
            assert charm.state.unit_server.peer_cert_ready

        def mock_rolling_restart(_):
            charm._restart(event)

        with (
            patch(
                "charms.tls_certificates_interface.v4.tls_certificates.TLSCertificatesRequiresV4.get_assigned_certificates",
                return_value=([client_provider_certificate], requirer_private_key),
            ),
            patch("charm.EtcdOperatorCharm.rolling_restart", mock_rolling_restart),
            patch("managers.cluster.ClusterManager.broadcast_peer_url"),
            patch("managers.config.ConfigManager._get_cluster_endpoints", return_value=""),
            patch("managers.cluster.ClusterManager.health_check", return_value=True),
        ):
            event.certificate = client_certificate
            charm.tls_events._on_certificate_available(event)

            assert charm.state.unit_server.client_cert_ready
            assert charm.state.unit_server.certs_ready
            assert charm.state.unit_server.tls_state == TLSState.TLS
