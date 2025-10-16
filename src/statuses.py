# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Statuses for the Charmed Etcd Operator.

This module defines various status enums that represent the state of the charm,
"""

from enum import Enum

from data_platform_helpers.advanced_statuses.models import StatusObject


class CharmStatuses(Enum):
    """Collection of possible statuses for the charm."""

    ACTIVE_IDLE = StatusObject(status="active", message="")
    NO_PEER_RELATION = StatusObject(status="maintenance", message="no peer relation available")
    PEER_URL_NOT_SET = StatusObject(status="maintenance", message="peer-url not set")
    SECRET_ACCESS_ERROR = StatusObject(
        status="blocked",
        message="Cannot access configured secret, check permissions",
        running="async",
    )


class BackupStatuses(Enum):
    """Collection of backup related statuses."""

    BACKUP_IN_PROGRESS = StatusObject(status="maintenance", message="Creating database backup...")
    BACKUP_S3_PARAMETERS_MISSING = StatusObject(
        status="blocked", message="Missing or invalid s3 credentials"
    )
    BACKUP_AZURE_PARAMETERS_MISSING = StatusObject(
        status="blocked", message="Missing or invalid azure credentials"
    )
    RESTORE_FAILED = StatusObject(status="blocked", message="failed to restore backup")
    RESTORE_VERIFICATION_FAILED = StatusObject(
        status="blocked",
        message="Restore verification failed - etcd cluster still running, restore cancelled, check debug-log",
    )
    RESTORE_IN_PROGRESS = StatusObject(
        status="maintenance", message="Database restore is in progress"
    )
    RESTORE_UNHEALTHY = StatusObject(
        status="blocked",
        message="cluster unhealthy after restoring backup - check debug-log",
    )
    OBJECT_STORAGE_CONFLICT = StatusObject(
        status="blocked", message="Azure and S3 storages configured - please remove one"
    )


class ClusterStatuses(Enum):
    """Collection of cluster related statuses."""

    CLUSTER_INITIALIZING = StatusObject(
        status="maintenance", message="Initializing etcd cluster..."
    )
    CLUSTER_FAILED = StatusObject(
        status="blocked",
        message="Cluster failure - majority of cluster members lost",
        action="Run action rebuild-cluster",
    )
    CLUSTER_MANAGEMENT_ERROR = StatusObject(
        status="blocked", message="cluster management error", running="async"
    )
    CLUSTER_NOT_INITIALIZED = StatusObject(
        status="blocked", message="Waiting for cluster initialization"
    )
    CLUSTER_NOT_JOINED = StatusObject(status="maintenance", message="Waiting to join cluster")
    CLUSTER_MEMBER_NOT_PROMOTED = StatusObject(
        status="maintenance", message="Waiting to promote learning member"
    )
    CLUSTER_REBUILD_IN_PROGRESS = StatusObject(
        status="blocked", message="Rebuilding with new cluster configuration..."
    )
    AUTHENTICATION_NOT_ENABLED = StatusObject(
        status="blocked", message="failed to enable authentication in etcd"
    )
    HEALTH_CHECK_FAILED = StatusObject(status="maintenance", message="health check failed")
    REMOVED = StatusObject(status="blocked", message="unit removed from cluster", running="async")
    RESTART_FAILED = StatusObject(
        status="maintenance", message="unhealthy after restarting", running="async"
    )
    PASSWORD_UPDATE_FAILED = StatusObject(
        status="blocked", message="failed to update password", running="async"
    )


class ConfigStatuses(Enum):
    """Collection of config related statuses."""

    TUNING_CONFIG_INVALID = StatusObject(
        status="blocked",
        message="Invalid values set for the config options: 'election-timeout', 'heartbeat-interval'",
    )


class TLSStatuses(Enum):
    """Collection of TLS related statuses."""

    TLS_DISABLING_PEER_TLS = StatusObject(status="maintenance", message="Disabling peer TLS...")
    TLS_DISABLING_CLIENT_TLS = StatusObject(
        status="maintenance", message="Disabling client TLS..."
    )
    TLS_ENABLING_PEER_TLS = StatusObject(status="maintenance", message="Enabling peer TLS...")
    TLS_ENABLING_CLIENT_TLS = StatusObject(status="maintenance", message="Enabling client TLS...")
    TLS_INVALID_PRIVATE_KEY = StatusObject(
        status="blocked",
        message="The private key provided is not valid. Please provide a valid private key",
    )
    TLS_NOT_READY = StatusObject(status="maintenance", message="Waiting for TLS to be ready")
    TLS_PEER_CA_ROTATING = StatusObject(status="maintenance", message="Rotating peer CA...")
    TLS_CLIENT_CA_ROTATING = StatusObject(status="maintenance", message="Rotating client CA...")
    TLS_CLIENT_CERTS_EXPIRING = StatusObject(
        status="maintenance",
        message="TLS client certificates expiring soon. Please ensure new certificates are provided",
        short_message="TLS client certificates expiring soon",
    )
    TLS_PEER_CERTS_EXPIRING = StatusObject(
        status="maintenance",
        message="TLS peer certificates expiring soon. Please ensure new certificates are provided",
        short_message="TLS peer certificates expiring soon",
    )
    CERT_REFRESH_IP_CHANGE = StatusObject(
        status="maintenance",
        message="Refreshing TLS certificates because of updated IP address",
        running="async",
    )
    SANS_CONFIG_INVALID = StatusObject(
        status="blocked",
        message="Invalid value for config option 'certificate-extra-sans'",
        short_message="Invalid value `certificate-extra-sans`",
    )
    CLIENT_DOMAIN_CONFIG_INVALID = StatusObject(
        status="blocked",
        message="Invalid value for config option 'client-certificate-domain'",
    )
    PEER_DOMAIN_CONFIG_INVALID = StatusObject(
        status="blocked",
        message="Invalid value for config option 'peer-certificate-domain'",
    )


class ExternalClientsStatuses(Enum):
    """Collection of external clients related statuses."""

    EC_INVALID_CERTIFICATE = StatusObject(
        status="blocked",
        message="Client relation: The certificate provided is a CA certificate. Please provide an end-entity certificate",
    )
    EC_MISSING_CREDENTIALS = StatusObject(
        status="blocked", message="Client relation: Missing certificate or prefix"
    )
    EC_USERNAME_EXISTS = StatusObject(
        status="blocked",
        message="Client relation: The username provided already exists. Please provide a unique username",
    )
    EC_TLS_IS_DISABLED = StatusObject(
        status="blocked", message="Client relation: TLS is disabled. Please enable TLS"
    )


class EtcdServiceStatuses(Enum):
    """Collection of etcd service related statuses."""

    SERVICE_INSTALLING = StatusObject(
        status="maintenance",
        message="Installing etcd...",
    )
    SERVICE_STARTING = StatusObject(
        status="maintenance", message="Waiting for etcd to start...", running="async"
    )
    SERVICE_NOT_INSTALLED = StatusObject(
        status="blocked",
        message="unable to install etcd snap",
        running="async",
    )
    SERVICE_NOT_RUNNING = StatusObject(
        status="blocked", message="etcd service not running", running="async"
    )
