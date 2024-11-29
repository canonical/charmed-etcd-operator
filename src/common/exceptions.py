#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific exceptions."""


class RaftLeaderNotFoundError(Exception):
    """Custom Exception if there is no current Raft leader."""

    pass


class EtcdUserNotCreatedError(Exception):
    """Custom Exception if user could not be added to etcd cluster."""

    pass


class EtcdAuthNotEnabledError(Exception):
    """Custom Exception if authentication could not be enabled in the etcd cluster."""

    pass
