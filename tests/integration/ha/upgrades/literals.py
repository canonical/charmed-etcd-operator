#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of literals for upgrades tests."""

NUM_UNITS = 3
CHARM_CHANNEL = "3.6/edge"
CHARM_REVISIONS_TO_DEPLOY = {"x86_64": 94, "aarch64": 95}
WORKLOAD_VERSION = {"previous": "3.6.2", "target": "3.6.4"}
CERTIFICATE_EXPIRY_TIME = 250
