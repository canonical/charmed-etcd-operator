#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implementation of WorkloadBase for running on VMs."""

import logging
from pathlib import Path

from charms.operator_libs_linux.v2 import snap
from tenacity import Retrying, retry, stop_after_attempt, wait_fixed
from typing_extensions import override

from core.workload import WorkloadBase
from literals import SNAP_NAME, SNAP_REVISION, SNAP_SERVICE

logger = logging.getLogger(__name__)


class EtcdWorkload(WorkloadBase):
    """Implementation of WorkloadBase for running on VMs."""

    def __init__(self):
        for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(5)):
            with attempt:
                self.etcd = snap.SnapCache()[SNAP_NAME]

    @override
    def start(self) -> None:
        try:
            self.etcd.start(services=[SNAP_SERVICE])
        except snap.SnapError as e:
            logger.exception(str(e))

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True)
    def install(self) -> bool:
        """Install the etcd snap from the snap store.

        Returns:
            True if successfully installed, False if any error occurs.
        """
        try:
            self.etcd.ensure(snap.SnapState.Present, revision=SNAP_REVISION)
            self.etcd.hold()
            return True
        except snap.SnapError as e:
            logger.error(str(e))
            return False

    @override
    def alive(self) -> bool:
        try:
            return bool(self.etcd.services[SNAP_SERVICE]["active"])
        except KeyError:
            return False

    @override
    def write_file(self, content: str, file: str) -> None:
        path = Path(file)
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(content)

    @override
    def stop(self) -> None:
        try:
            self.etcd.stop(services=[SNAP_SERVICE])
        except snap.SnapError as e:
            logger.exception(str(e))

    @override
    def restart(self) -> None:
        self.etcd.restart(services=[SNAP_SERVICE])

    @override
    def remove_file(self, file) -> None:
        path = Path(file)
        path.unlink(missing_ok=True)
