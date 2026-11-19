#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of literals for upgrades tests."""

NUM_UNITS = 3
CHARM_CHANNEL = "3.6/edge"
CHARM_REVISIONS_TO_DEPLOY = {"x86_64": 193, "aarch64": 194}
WORKLOAD_VERSION = {"previous": "3.6.13", "target": "3.6.16"}
CERTIFICATE_EXPIRY_TIME = 250
