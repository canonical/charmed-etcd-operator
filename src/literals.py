#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the etcd charm."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase

SNAP_NAME = "charmed-etcd"
SNAP_REVISIONS = {"x86_64": 13, "aarch64": 16}
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

DATA_STORAGE = "data"
PEER_RELATION = "etcd-peers"
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

ELECTION_TIMEOUT_CONFIG = "election-timeout"
HEARTBEAT_INTERVAL_CONFIG = "heartbeat-interval"

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


class Status(Enum):
    """Collection of possible statuses for the charm."""

    ACTIVE = StatusLevel(ActiveStatus(), "DEBUG")
    AUTHENTICATION_NOT_ENABLED = StatusLevel(
        BlockedStatus("failed to enable authentication in etcd"), "ERROR"
    )
    BACKUP_IN_PROGRESS = StatusLevel(MaintenanceStatus("Creating database backup..."), "DEBUG")
    BACKUP_S3_PARAMETERS_MISSING = StatusLevel(
        BlockedStatus("Missing required parameters in the s3 relation."), "ERROR"
    )
    BACKUP_AZURE_PARAMETERS_MISSING = StatusLevel(
        BlockedStatus("Missing required parameters in the azure relation."), "ERROR"
    )
    CLUSTER_INITIALIZING = StatusLevel(MaintenanceStatus("Initializing etcd cluster..."), "DEBUG")
    CLUSTER_FAILED = StatusLevel(
        BlockedStatus(
            "Cluster failure - majority of cluster members lost. Run action `rebuild-cluster` to recover."
        ),
        "ERROR",
    )
    CLUSTER_MANAGEMENT_ERROR = StatusLevel(BlockedStatus("cluster management error"), "ERROR")
    CLUSTER_NOT_INITIALIZED = StatusLevel(
        BlockedStatus("Waiting for cluster initialization"), "ERROR"
    )
    CLUSTER_NOT_JOINED = StatusLevel(MaintenanceStatus("Waiting to join cluster"), "DEBUG")
    CLUSTER_MEMBER_NOT_PROMOTED = StatusLevel(
        MaintenanceStatus("Waiting to promote learning member"), "DEBUG"
    )
    CLUSTER_REBUILD_IN_PROGRESS = StatusLevel(
        BlockedStatus("Rebuilding with new cluster configuration..."), "ERROR"
    )
    HEALTH_CHECK_FAILED = StatusLevel(MaintenanceStatus("health check failed"), "DEBUG")
    NO_PEER_RELATION = StatusLevel(MaintenanceStatus("no peer relation available"), "DEBUG")
    OBJECT_STORAGE_CONFLICT = StatusLevel(
        BlockedStatus("Azure and S3 storages configured - please remove one"), "ERROR"
    )
    PASSWORD_UPDATE_FAILED = StatusLevel(BlockedStatus("failed to update password"), "ERROR")
    PEER_URL_NOT_SET = StatusLevel(MaintenanceStatus("peer-url not set"), "DEBUG")
    REMOVED = StatusLevel(BlockedStatus("unit removed from cluster"), "INFO")
    RESTORE_FAILED = StatusLevel(BlockedStatus("failed to restore backup"), "ERROR")
    RESTORE_VERIFICATION_FAILED = StatusLevel(
        BlockedStatus(
            "Restore verification failed - etcd cluster still running, restore cancelled, check debug-log"
        ),
        "ERROR",
    )
    RESTORE_IN_PROGRESS = StatusLevel(
        MaintenanceStatus("Database restore is in progress"), "ERROR"
    )
    RESTORE_UNHEALTHY = StatusLevel(
        BlockedStatus("cluster unhealthy after restoring backup - check debug-log"), "ERROR"
    )
    TLS_DISABLING_PEER_TLS = StatusLevel(MaintenanceStatus("Disabling peer TLS..."), "DEBUG")
    TLS_DISABLING_CLIENT_TLS = StatusLevel(MaintenanceStatus("Disabling client TLS..."), "DEBUG")
    TLS_ENABLING_PEER_TLS = StatusLevel(MaintenanceStatus("Enabling peer TLS..."), "DEBUG")
    TLS_ENABLING_CLIENT_TLS = StatusLevel(MaintenanceStatus("Enabling client TLS..."), "DEBUG")
    TLS_INVALID_PRIVATE_KEY = StatusLevel(
        BlockedStatus("The private key provided is not valid. Please provide a valid private key"),
        "ERROR",
    )
    TLS_NOT_READY = StatusLevel(MaintenanceStatus("Waiting for TLS to be ready"), "DEBUG")
    TLS_PEER_CA_ROTATING = StatusLevel(MaintenanceStatus("Rotating peer CA..."), "DEBUG")
    TLS_CLIENT_CA_ROTATING = StatusLevel(MaintenanceStatus("Rotating client CA..."), "DEBUG")
    TLS_CLIENT_CERTS_EXPIRING = StatusLevel(
        MaintenanceStatus(
            "TLS client certificates expiring soon. Please ensure new certificates are provided."
        ),
        "WARNING",
    )
    TLS_PEER_CERTS_EXPIRING = StatusLevel(
        MaintenanceStatus(
            "TLS peer certificates expiring soon. Please ensure new certificates are provided."
        ),
        "WARNING",
    )
    TUNING_CONFIG_INVALID = StatusLevel(
        BlockedStatus(
            "Invalid values set for the config options: 'election-timeout', 'heartbeat-interval'"
        ),
        "ERROR",
    )
    SERVICE_INSTALLING = StatusLevel(MaintenanceStatus("Installing etcd..."), "DEBUG")
    SERVICE_STARTING = StatusLevel(MaintenanceStatus("Waiting for etcd to start..."), "DEBUG")
    SERVICE_NOT_INSTALLED = StatusLevel(BlockedStatus("unable to install etcd snap"), "ERROR")
    SERVICE_NOT_RUNNING = StatusLevel(BlockedStatus("etcd service not running"), "ERROR")
    EC_INVALID_CERTIFICATE = StatusLevel(
        MaintenanceStatus(
            "Client relation: The certificate provided is a CA certificate. Please provide an end-entity certificate"
        ),
        "ERROR",
    )
    EC_MISSING_CREDENTIALS = StatusLevel(
        MaintenanceStatus("Client relation: Missing certificate or prefix."), "ERROR"
    )
    EC_USERNAME_EXISTS = StatusLevel(
        MaintenanceStatus(
            "Client relation: The username provided already exists. Please provide a unique username"
        ),
        "ERROR",
    )
    EC_TLS_IS_DISABLED = StatusLevel(
        MaintenanceStatus("Client relation: TLS is disabled. Please enable TLS"),
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
