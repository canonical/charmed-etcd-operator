#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the etcd charm."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase

SNAP_NAME = "etcd"
SNAP_REVISION = 233
SNAP_SERVICE = "etcd.etcd"

PEER_RELATION = "etcd-cluster"
SERVER_PORT = 2379
PEER_PORT = 2380

DebugLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
SUBSTRATES = Literal["vm", "k8s"]
SUBSTRATE = "vm"


@dataclass
class StatusLevel:
    """Status object helper."""

    status: StatusBase
    log_level: DebugLevel


class Status(Enum):
    """Collection of possible statuses for the charm."""

    ACTIVE = StatusLevel(ActiveStatus(), "DEBUG")
    SERVICE_NOT_INSTALLED = StatusLevel(BlockedStatus("unable to install etcd snap"), "ERROR")
    SERVICE_NOT_RUNNING = StatusLevel(BlockedStatus("etcd service not running"), "ERROR")
    NO_PEER_RELATION = StatusLevel(MaintenanceStatus("no peer relation available"), "DEBUG")
