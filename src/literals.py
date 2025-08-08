#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the etcd charm."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ops.model import StatusBase

SNAP_NAME = "charmed-etcd"
SNAP_SERVICE = "etcd"
SNAP_DATA_PATH = "/var/snap/charmed-etcd/common/var/lib/etcd"
SNAP_LOG_PATH = "/var/snap/charmed-etcd/common/var/log/etcd"
SNAP_ARCHIVE_PATH = "/var/snap/charmed-etcd/common/archive"
SNAP_CONFIG_PATH = "/var/snap/charmed-etcd/current"
SNAP_USER = 584788
SNAP_GROUP = "root"
CONFIG_FILE = "/var/snap/charmed-etcd/current/etcd.conf.yml"
TLS_ROOT_DIR = "/var/snap/charmed-etcd/current/tls"
DATABASE_DIR = "/var/snap/charmed-etcd/common/var/lib/etcd/member"
BACKUP_FILE_NAME = "/var/snap/charmed-etcd/common/archive/charmed-etcd_snapshot.db"
BACKUP_ID_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
VERSIONS_FILE = "refresh_versions.toml"

DATA_STORAGE = "data"
PEER_RELATION = "etcd-peers"
STATUS_PEERS_RELATION = "status-peers"
RESTART_RELATION = "restart"
EXTERNAL_CLIENTS_RELATION = "etcd-client"
CERTIFICATE_TRANSFER_RELATION = "client-cas"
CLIENT_PORT = 2379
PEER_PORT = 2380
METRICS_PORT = 9100

INTERNAL_USER = "root"
INTERNAL_USER_PASSWORD_CONFIG = "system-users"
SECRETS_APP = ["root-password", "s3-credentials", "azure-credentials"]

DebugLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
SUBSTRATES = Literal["vm", "k8s"]
SUBSTRATE = "vm"

PEER_TLS_RELATION_NAME = "peer-certificates"
CLIENT_TLS_RELATION_NAME = "client-certificates"
TLS_PEER_PRIVATE_KEY_CONFIG = "tls-peer-private-key"
TLS_CLIENT_PRIVATE_KEY_CONFIG = "tls-client-private-key"

S3_RELATION_NAME = "s3-credentials"
AZURE_RELATION_NAME = "azure-credentials"


@dataclass
class StatusLevel:
    """Status object helper."""

    status: StatusBase
    log_level: DebugLevel


class EtcdClusterState(Enum):
    """Enum for Cluster state in etcd."""

    EXISTING = "existing"
    NEW = "new"


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


# enum for Backup state
class RestoreStep(Enum):
    """Backup / Restore workflow step representation."""

    NOT_STARTED = ""
    DOWNLOAD = "download_backup"
    STOP = "stop_workload"
    VERIFY = "verify_backup"
    RESTORE = "restore_backup"
    START = "restart_workload"
    COMPLETED = "completed"


class TuningOptions(Enum):
    """Configuration options for tuning etcd performance."""

    ELECTION_TIMEOUT_CONFIG = "election-timeout"
    HEARTBEAT_INTERVAL_CONFIG = "heartbeat-interval"
