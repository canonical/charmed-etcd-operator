#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implementation of WorkloadBase for running on VMs."""

import logging
import subprocess
from os.path import exists
from pathlib import Path
from platform import machine
from shutil import copyfile, rmtree
from socket import socket
from typing import Any, Dict, List

import tomllib
import yaml
from charms.operator_libs_linux.v1.systemd import service_disable, service_enable
from charms.operator_libs_linux.v2 import snap
from tenacity import Retrying, retry, retry_if_exception_type, stop_after_attempt, wait_fixed
from typing_extensions import override

from common.exceptions import EtcdServiceError
from core.workload import WorkloadBase
from literals import SNAP_NAME, SNAP_SERVICE, VERSIONS_FILE

logger = logging.getLogger(__name__)

WORKING_DIR = Path(__file__).absolute().parent


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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(5),
        reraise=True,
        retry=retry_if_exception_type(EtcdServiceError),
    )
    def install(self, revision: str | None = None, retry_and_raise: bool = True) -> bool:
        """Install the etcd snap from the snap store.

        Args:
            revision (str | None): the snap revision to install. Will be loaded from the
                `refresh_versions.toml` file if None.
            retry_and_raise (bool): whether to retry in case of errors. Will raise if the error
                persists.

        Returns:
            True if successfully installed, False if errors occur and `retry_and_raise` is False.
        """
        if not revision:
            versions = self.load_toml_file(f"{WORKING_DIR}/../{VERSIONS_FILE}")
            revision = versions["snap"]["revisions"][machine()]

        try:
            self.etcd.ensure(snap.SnapState.Present, revision=revision)
            self.etcd.hold()
            return True
        except snap.SnapError as e:
            logger.error(str(e))
            if retry_and_raise:
                raise EtcdServiceError(e)
            return False

    @override
    def alive(self) -> bool:
        try:
            return bool(self.etcd.services[SNAP_SERVICE]["active"])
        except KeyError:
            return False

    @override
    def is_reachable(self, host: str, port: int) -> bool:
        s = socket()
        s.settimeout(5)

        try:
            for attempt in Retrying(stop=stop_after_attempt(5), wait=wait_fixed(3), reraise=True):
                with attempt:
                    s.connect((host, port))
            return True
        except Exception as e:
            logger.debug(f"Connection to {host}:{port} fails with: {e}")
            return False
        finally:
            s.close()

    @override
    def write_file(self, content: str, file: str) -> None:
        path = Path(file)
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(content)

    @override
    def load_yaml_file(self, file: str) -> Dict[str, Any]:
        if not exists(file):
            return {}

        with open(file, "r") as f:
            return yaml.safe_load(f)

    @override
    def load_toml_file(self, file: str) -> Dict[str, Any]:
        if not exists(file):
            return {}

        with open(file, "rb") as f:
            return tomllib.load(f)

    @override
    def stop(self) -> None:
        self.etcd.stop(services=[SNAP_SERVICE])

    @override
    def restart(self) -> None:
        self.etcd.restart(services=[SNAP_SERVICE])

    @override
    def copy_file(self, src_file: str, dst_file: str) -> None:
        copyfile(src_file, dst_file)

    @override
    def remove_file(self, file) -> None:
        path = Path(file)
        path.unlink(missing_ok=True)

    @override
    def remove_directory(self, directory: str) -> None:
        rmtree(directory)

    @override
    def exists(self, path: str) -> bool:
        path_object = Path(path)

        if path_object.exists():
            if path_object.is_dir():
                # consider it false if the directory is empty
                return len(list(path_object.glob("*"))) > 0
            return True

        return False

    @override
    def exec(self, command: List[str]) -> str:
        try:
            output = subprocess.run(
                command,
                check=True,
                text=True,
                capture_output=True,
                timeout=10,
            ).stdout.strip()
            logger.debug(output)
            return output
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error(e)
            raise

    @override
    def disable_service(self) -> None:
        service_disable(f"snap.{SNAP_NAME}.{SNAP_SERVICE}")

    @override
    def enable_service(self) -> None:
        service_enable(f"snap.{SNAP_NAME}.{SNAP_SERVICE}")

    @override
    def disable_database(self) -> None:
        self.disable_service()
        self.stop()

    @override
    def enable_database(self) -> None:
        self.enable_service()
        self.start()

    def snap_revision(self) -> str:
        """Get the snap revision that is currently installed."""
        return self.etcd.revision

    @override
    def memory_size(self) -> int:
        """Get the total memory size of the system in Bytes.

        Read the /proc/meminfo file and return the values.
        According to the kernel source code, the values are always in kB:
            https://github.com/torvalds/linux/blob/
                2a130b7e1fcdd83633c4aa70998c314d7c38b476/fs/proc/meminfo.c#L31

        Returns:
            float: The total memory size in Bytes.
        """
        with open("/proc/meminfo") as f:
            meminfo = f.read().split("\n")
            meminfo = [line.split() for line in meminfo if line.strip()]

        memory_sizes = {line[0][:-1]: float(line[1]) for line in meminfo}
        return int(memory_sizes["MemTotal"] * 1024)  # convert from kB to Bytes
