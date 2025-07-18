#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent / "src"))
sys.path.append(str(Path(__file__).parent.parent.parent / "lib"))

import pytest
import pathlib
import shutil
import tomli
import tomli_w
import platform

from ops import testing

from charm import EtcdOperatorCharm
from core.models import Member
from literals import CLIENT_TLS_RELATION_NAME, PEER_RELATION, PEER_TLS_RELATION_NAME

MEMBER_LIST_DICT = {
    "charmed-etcd0": Member(
        id="0",
        name="charmed-etcd0",
        peer_urls=["http://ip0:2380"],
        client_urls=["http://ip0:2379"],
    ),
    "charmed-etcd1": Member(
        id="1",
        name="charmed-etcd1",
        peer_urls=["http://ip1:2380"],
        client_urls=["http://ip1:2379"],
    ),
    "charmed-etcd2": Member(
        id="2",
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
                    f"{member.name}={member.peer_urls[0].replace('http://', 'https://')}"
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

class _MockRefresh:
    in_progress = False
    next_unit_allowed_to_refresh = True
    workload_allowed_to_start = True
    app_status_higher_priority = None
    unit_status_higher_priority = None

    def __init__(self, _, /):
        pass

    def update_snap_revision(self):
        pass

    @property
    def pinned_snap_revision(self):
        with pathlib.Path("refresh_versions.toml").open("rb") as file:
            return tomli.load(file)["snap"]["revisions"][platform.machine()]

    def unit_status_lower_priority(self, *, workload_is_running=True):
        return None


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr("charm_refresh.Machines", _MockRefresh)

    # Add charm version to refresh_versions.toml
    path = pathlib.Path("refresh_versions.toml")
    backup = pathlib.Path("refresh_versions.toml.backup")
    shutil.copy(path, backup)
    with path.open("rb") as file:
        versions = tomli.load(file)
    versions["charm"] = "16/0.0.0"
    with path.open("wb") as file:
        tomli_w.dump(versions, file)

    yield

    path.unlink()
    shutil.move(backup, path)