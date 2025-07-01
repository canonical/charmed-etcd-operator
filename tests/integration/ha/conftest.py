# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from platform import machine

import pytest

from literals import SNAP_NAME, SNAP_REVISIONS


@pytest.fixture
def etcd_process() -> str:
    return f"/snap/{SNAP_NAME}/{SNAP_REVISIONS[machine()]}/bin/etcd"
