#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

from ops import testing

from charm import EtcdOperatorCharm
from literals import CLIENT_TLS_RELATION_NAME, PEER_RELATION, PEER_TLS_RELATION_NAME, Status


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
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)

    state_in = testing.State(
        relations=[peer_relation, peer_tls_relation, client_tls_relation],
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

    with (
        patch("managers.cluster.ClusterManager.broadcast_peer_url"),
        patch("managers.cluster.ClusterManager.health_check", return_value=True),
        patch("managers.tls.TLSManager.delete_certificates"),
        patch("managers.config.ConfigManager.set_config_properties"),
        patch("workload.EtcdWorkload.restart"),
    ):
        state_out = ctx.run(ctx.on.relation_broken(relation=peer_tls_relation), state_in)
        state_out = ctx.run(ctx.on.relation_broken(relation=client_tls_relation), state_out)
        assert state_out.unit_status == Status.ACTIVE.value.status
        assert state_out.get_relation(peer_relation.id).local_unit_data["tls-state"] == "no-tls"
        assert (
            state_out.get_relation(peer_relation.id).local_unit_data["client-cert-ready"]
            == "False"
        )
        assert (
            state_out.get_relation(peer_relation.id).local_unit_data["peer-cert-ready"] == "False"
        )

    with (
        patch("managers.cluster.ClusterManager.broadcast_peer_url"),
        patch("managers.cluster.ClusterManager.health_check", return_value=False),
        patch("managers.tls.TLSManager.delete_certificates"),
        patch("managers.config.ConfigManager.set_config_properties"),
        patch("workload.EtcdWorkload.restart"),
    ):
        state_out = ctx.run(ctx.on.relation_broken(relation=peer_tls_relation), state_in)
        state_out = ctx.run(ctx.on.relation_broken(relation=client_tls_relation), state_out)
        assert state_out.unit_status == Status.HEALTH_CHECK_FAILED.value.status
