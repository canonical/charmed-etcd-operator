# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from platform import machine

import pytest
import toml

from literals import SNAP_NAME


@pytest.fixture
def etcd_process() -> str:
    versions = toml.load("./refresh_versions.toml")
    return f"/snap/{SNAP_NAME}/{versions['snap.revisions'][machine()]}/bin/etcd"
