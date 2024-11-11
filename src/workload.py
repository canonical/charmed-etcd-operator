#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implementation of WorkloadBase for running on VMs."""

import logging

from charms.operator_libs_linux.v2 import snap
from typing_extensions import override

from core.workload import WorkloadBase
from literals import SNAP_NAME, SNAP_REVISION

logger = logging.getLogger(__name__)


class EtcdWorkload(WorkloadBase):
    """Implementation of WorkloadBase for running on VMs."""

    def __init__(self):
        self.etcd = snap.SnapCache()[SNAP_NAME]

    @override
    def start(self) -> None:
        pass

    def install(self) -> bool:
        """Install the etcd snap from the snap store.

        Returns:
            True if successfully installed, False if any error occurs.
        """
        try:
            self.etcd.ensure(snap.SnapState.Present, revision=SNAP_REVISION)
            self.etcd.hold()
        except snap.SnapError as e:
            logger.error(str(e))
            return False
