#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the etcd charm."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase

SNAP_NAME = "charmed-etcd"
SNAP_REVISION = 2
SNAP_SERVICE = "etcd"
SNAP_DATA_PATH = "/var/snap/charmed-etcd/common/var/lib/etcd"
SNAP_USER = 584788
SNAP_GROUP = "root"
CONFIG_FILE = "/var/snap/charmed-etcd/current/etcd.conf.yml"
TLS_ROOT_DIR = "/var/snap/charmed-etcd/common/tls"
DATABASE_DIR = "/var/snap/charmed-etcd/common/var/lib/etcd/member"

DATA_STORAGE = "data"
PEER_RELATION = "etcd-peers"
RESTART_RELATION = "restart"
CLIENT_PORT = 2379
PEER_PORT = 2380

INTERNAL_USER = "root"
INTERNAL_USER_PASSWORD_CONFIG = "system-users"
SECRETS_APP = ["root-password"]

DebugLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
SUBSTRATES = Literal["vm", "k8s"]
SUBSTRATE = "vm"

PEER_TLS_RELATION_NAME = "peer-certificates"
CLIENT_TLS_RELATION_NAME = "client-certificates"
TLS_PEER_PRIVATE_KEY_CONFIG = "tls-peer-private-key"
TLS_CLIENT_PRIVATE_KEY_CONFIG = "tls-client-private-key"


@dataclass
class StatusLevel:
    """Status object helper."""

    status: StatusBase
    log_level: DebugLevel


class EtcdClusterState(Enum):
    """Enum for Cluster state in etcd."""

    EXISTING = "existing"
    NEW = "new"


class Status(Enum):
    """Collection of possible statuses for the charm."""

    ACTIVE = StatusLevel(ActiveStatus(), "DEBUG")
    AUTHENTICATION_NOT_ENABLED = StatusLevel(
        BlockedStatus("failed to enable authentication in etcd"), "ERROR"
    )
    SERVICE_NOT_INSTALLED = StatusLevel(BlockedStatus("unable to install etcd snap"), "ERROR")
    SERVICE_NOT_RUNNING = StatusLevel(BlockedStatus("etcd service not running"), "ERROR")
    NO_PEER_RELATION = StatusLevel(MaintenanceStatus("no peer relation available"), "DEBUG")
    CLUSTER_MANAGEMENT_ERROR = StatusLevel(BlockedStatus("cluster management error"), "ERROR")
    REMOVED = StatusLevel(BlockedStatus("unit removed from cluster"), "INFO")
    HEALTH_CHECK_FAILED = StatusLevel(MaintenanceStatus("health check failed"), "DEBUG")
    PEER_URL_NOT_SET = StatusLevel(MaintenanceStatus("peer-url not set"), "DEBUG")
    TLS_ENABLING_PEER_TLS = StatusLevel(MaintenanceStatus("Enabling peer TLS..."), "DEBUG")
    TLS_ENABLING_CLIENT_TLS = StatusLevel(MaintenanceStatus("Enabling client TLS..."), "DEBUG")
    TLS_DISABLING_PEER_TLS = StatusLevel(MaintenanceStatus("Disabling peer TLS..."), "DEBUG")
    TLS_DISABLING_CLIENT_TLS = StatusLevel(MaintenanceStatus("Disabling client TLS..."), "DEBUG")
    TLS_CLIENT_TRANSITION_FAILED = StatusLevel(
        BlockedStatus("Failed to transition to/from client tls"), "ERROR"
    )
    TLS_PEER_TRANSITION_FAILED = StatusLevel(
        BlockedStatus("Failed to transition to/from peer tls"), "ERROR"
    )
    TLS_INVALID_PRIVATE_KEY = StatusLevel(
        BlockedStatus("The private key provided is not valid. Please provide a valid private key"),
        "ERROR",
    )


# enum for TLS state
class TLSState(Enum):
    """Enum for TLS state."""

    NO_TLS = "no-tls"
    TO_TLS = "to-tls"
    TLS = "tls"
    TO_NO_TLS = "to-no-tls"


class TLSType(Enum):
    """TLS types."""

    PEER = "peer"
    CLIENT = "client"


class TLSCARotationState(Enum):
    """TLS CA Rotation state."""

    NO_ROTATION = "no-rotation"
    NEW_CA_DETECTED = "new-ca-detected"
    NEW_CA_ADDED = "new-ca-added"
    CERT_UPDATED = "cert-updated"
