#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific exceptions."""


class RaftLeaderNotFoundError(Exception):
    """Custom Exception if there is no current Raft leader."""


class EtcdUserManagementError(Exception):
    """Custom Exception if user could not be added or updated in etcd cluster."""


class EtcdAuthNotEnabledError(Exception):
    """Custom Exception if authentication could not be enabled in the etcd cluster."""


class TLSMissingCertificateOrKeyError(Exception):
    """Custom Exception if a TLS certificate or key is missing."""


class HealthCheckFailedError(Exception):
    """Custom Exception if a health check failed."""
