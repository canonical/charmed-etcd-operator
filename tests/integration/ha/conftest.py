# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from platform import machine

import pytest
import tomllib

from literals import SNAP_NAME


@pytest.fixture
def etcd_process() -> str:
    with open("./refresh_versions.toml", "rb") as f:
        versions = tomllib.load(f)
    return f"/snap/{SNAP_NAME}/{versions['snap']['revisions'][machine()]}/bin/etcd"
