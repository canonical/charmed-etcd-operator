#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the etcd charm."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase

SNAP_NAME = "charmed-etcd"
SNAP_REVISION = 1
SNAP_SERVICE = "etcd"
# this path will be updated when we switch to charmed-etcd snap
# it's the current config path for the legacy-etcd snap
CONFIG_FILE = "/var/snap/charmed-etcd/current/etcd.conf.yml"
TLS_ROOT_DIR = "/var/snap/charmed-etcd/common/tls"

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


@dataclass
class StatusLevel:
    """Status object helper."""

    status: StatusBase
    log_level: DebugLevel


class Status(Enum):
    """Collection of possible statuses for the charm."""

    ACTIVE = StatusLevel(ActiveStatus(), "DEBUG")
    AUTHENTICATION_NOT_ENABLED = StatusLevel(
        BlockedStatus("failed to enable authentication in etcd"), "ERROR"
    )
    SERVICE_NOT_INSTALLED = StatusLevel(BlockedStatus("unable to install etcd snap"), "ERROR")
    SERVICE_NOT_RUNNING = StatusLevel(BlockedStatus("etcd service not running"), "ERROR")
    NO_PEER_RELATION = StatusLevel(MaintenanceStatus("no peer relation available"), "DEBUG")
    HEALTH_CHECK_FAILED = StatusLevel(MaintenanceStatus("health check failed"), "DEBUG")
    PEER_URL_NOT_SET = StatusLevel(MaintenanceStatus("peer-url not set"), "DEBUG")
    TLS_MISSING_CERTIFICATES = StatusLevel(MaintenanceStatus("missing certificates"), "DEBUG")
    TLS_NOT_READY = StatusLevel(MaintenanceStatus("Waiting for TLS to be configured..."), "DEBUG")
    TLS_DISABLING = StatusLevel(MaintenanceStatus("Disabling TLS..."), "DEBUG")
    TLS_CLIENT_TLS_MISSING = StatusLevel(BlockedStatus("client tls relation missing"), "DEBUG")
    TLS_PEER_TLS_MISSING = StatusLevel(BlockedStatus("peer tls relation missing"), "DEBUG")
    TLS_CLIENT_TLS_NEEDS_TO_BE_REMOVED = StatusLevel(
        MaintenanceStatus("client tls needs to be removed or peer tls needs to be integrated"),
        "DEBUG",
    )
    TLS_PEER_TLS_NEEDS_TO_BE_REMOVED = StatusLevel(
        MaintenanceStatus("peer tls needs to be removed or client tls needs to be integrated"),
        "DEBUG",
    )
    TLS_TRANSITION_FAILED = StatusLevel(
        BlockedStatus("failed to transition to/from tls state"), "ERROR"
    )


# enum for TLS state
class TLSState(Enum):
    """Enum for TLS state."""

    NO_TLS = "no-tls"
    TO_TLS = "to-tls"
    TLS = "tls"
    TO_NO_TLS = "to-no-tls"
    UPDATING_CLIENT_CERTS = "updating-client-tls"
    UPDATING_PEER_CERTS = "updating-peer-tls"
