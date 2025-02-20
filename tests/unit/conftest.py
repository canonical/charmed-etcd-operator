#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from ops import testing

from charm import EtcdOperatorCharm
from core.models import Member
from literals import CLIENT_TLS_RELATION_NAME, PEER_RELATION, PEER_TLS_RELATION_NAME

MEMBER_LIST_DICT = {
    "charmed-etcd0": Member(
        id="1",
        name="charmed-etcd0",
        peer_urls=["http://ip0:2380"],
        client_urls=["http://ip0:2379"],
    ),
    "charmed-etcd1": Member(
        id="2",
        name="charmed-etcd1",
        peer_urls=["http://ip1:2380"],
        client_urls=["http://ip1:2379"],
    ),
    "charmed-etcd2": Member(
        id="3",
        name="charmed-etcd2",
        peer_urls=["http://ip2:2380"],
        client_urls=["http://ip2:2379"],
    ),
}


@pytest.fixture
def cluster_tls_context():
    """Create a context for testing certificate available event."""
    current_unit = MEMBER_LIST_DICT["charmed-etcd0"]
    peer_units = [member for member in MEMBER_LIST_DICT.values() if member != current_unit]
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
            "cluster_members": ",".join(
                [
                    member.peer_urls[0].replace("http://", "https://")
                    for member in MEMBER_LIST_DICT.values()
                ]
            ),
            "endpoints": ",".join(
                [
                    member.client_urls[0].replace("http://", "https://")
                    for member in MEMBER_LIST_DICT.values()
                ]
            ),
        },
        local_unit_data={
            "ip": current_unit.client_urls[0].replace("http://", "").replace(":2379", ""),
            "hostname": current_unit.name,
            "client_cert_ready": "True",
            "peer_cert_ready": "True",
            "tls_client_state": "tls",
            "tls_peer_state": "tls",
        },
        peers_data={
            int(member.id): {
                "client_cert_ready": "True",
                "hostname": member.name,
                "ip": member.client_urls[0].replace("http://", "").replace(":2379", ""),
                "peer_cert_ready": "True",
                "tls_client_state": "tls",
                "tls_peer_state": "tls",
            }
            for member in peer_units
        },
    )
    peer_tls_relation = testing.Relation(id=2, endpoint=PEER_TLS_RELATION_NAME)
    client_tls_relation = testing.Relation(id=3, endpoint=CLIENT_TLS_RELATION_NAME)
    restart_relation = testing.PeerRelation(id=4, endpoint="restart")

    return ctx, [peer_relation, peer_tls_relation, client_tls_relation, restart_relation]


@pytest.fixture
def cluster_no_tls_context():
    """Create a context for a cluster without TLS."""
    current_unit = MEMBER_LIST_DICT["charmed-etcd0"]
    peer_units = [member for member in MEMBER_LIST_DICT.values() if member != current_unit]
    ctx = testing.Context(EtcdOperatorCharm)
    peer_relation = testing.PeerRelation(
        id=1,
        endpoint=PEER_RELATION,
        local_app_data={
            "authentication": "enabled",
            "cluster_state": "existing",
            "cluster_members": ",".join(
                [member.peer_urls[0] for member in MEMBER_LIST_DICT.values()]
            ),
            "endpoints": ",".join([member.client_urls[0] for member in MEMBER_LIST_DICT.values()]),
        },
        local_unit_data={
            "ip": current_unit.client_urls[0].replace("https://", "").replace(":2379", ""),
            "hostname": current_unit.name,
        },
        peers_data={
            int(member.id): {
                "ip": member.client_urls[0].replace("https://", "").replace(":2379", ""),
                "hostname": member.name,
            }
            for member in peer_units
        },
    )
    restart_relation = testing.PeerRelation(id=4, endpoint="restart")

    return ctx, [peer_relation, restart_relation]
