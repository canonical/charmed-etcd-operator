# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest

from literals import SNAP_NAME, SNAP_REVISIONS


@pytest.fixture
def etcd_process(platform: str) -> str:
    return f"/snap/{SNAP_NAME}/{SNAP_REVISIONS[platform]}/bin/etcd"
