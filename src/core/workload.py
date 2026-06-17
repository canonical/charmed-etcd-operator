#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base objects for workload operations across different substrates."""

import logging
import secrets
import socket
import string
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List

import tomllib
import yaml
from charmlibs.pathops import PathProtocol

from literals import BACKUP_FILE_NAME, CONFIG_FILE, DATABASE_DIR, TLS_ROOT_DIR

logger = logging.getLogger(__name__)


@dataclass
class TLSPaths:
    """Paths for TLS."""

    def __init__(self, root_dir: PathProtocol):
        self.tls_root = root_dir / TLS_ROOT_DIR

    @property
    def peer_ca(self) -> PathProtocol:
        """Path to the peer CA."""
        return self.tls_root / "peer_ca.pem"

    @property
    def peer_cert(self) -> PathProtocol:
        """Path to the peer cert."""
        return self.tls_root / "peer.pem"

    @property
    def peer_key(self) -> PathProtocol:
        """Path to the peer key."""
        return self.tls_root / "peer.key"

    @property
    def client_ca(self) -> PathProtocol:
        """Path to the client CA."""
        return self.tls_root / "client_ca.pem"

    @property
    def client_cert(self) -> PathProtocol:
        """Path to the server cert."""
        return self.tls_root / "client.pem"

    @property
    def client_key(self) -> PathProtocol:
        """Path to the server key."""
        return self.tls_root / "client.key"

    @property
    def backup_ca(self) -> PathProtocol:
        """Path to the CA for backup/restore object storage."""
        return self.tls_root / "backup_ca.pem"


@dataclass
class EtcdPaths:
    """Paths for etcd."""

    def __init__(self, root_dir: PathProtocol):
        self.root_dir = root_dir

    @property
    def config_file(self) -> PathProtocol:
        """Path to the etcd config file."""
        return self.root_dir / CONFIG_FILE

    @property
    def tls(self) -> TLSPaths:
        """TLS paths."""
        return TLSPaths(root_dir=self.root_dir)

    @property
    def data_dir(self) -> PathProtocol:
        """Path to the etcd database dir."""
        return self.root_dir / DATABASE_DIR

    @property
    def backup_file(self) -> PathProtocol:
        """Path to the etcd snapshot file."""
        return self.root_dir / BACKUP_FILE_NAME


class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    root_dir: PathProtocol

    @property
    def paths(self) -> EtcdPaths:
        """Object to access workload paths."""
        return EtcdPaths(root_dir=self.root_dir)

    @abstractmethod
    def start(self) -> None:
        """Start the workload service."""
        pass

    @abstractmethod
    def alive(self) -> bool:
        """Check if the workload is running.

        Returns:
            bool: True if the workload is running, False otherwise.
        """
        pass

    @abstractmethod
    def is_reachable(self, host: str, port: int) -> bool:
        """Check if the workload has started and is listening on the client port.

        Returns:
            bool: True if the workload is up, False otherwise.
        """

    @abstractmethod
    def stop(self) -> None:
        """Stop the workload service."""
        pass

    @staticmethod
    def generate_password() -> str:
        """Create randomized string for use as app passwords.

        Returns:
            str: String of 32 randomized letter+digit characters
        """
        return "".join([secrets.choice(string.ascii_letters + string.digits) for _ in range(32)])

    @abstractmethod
    def restart(self) -> None:
        """Restart the workload service."""
        pass

    @abstractmethod
    def exec(self, command: List[str]) -> str:
        """Run a command on the workload substrate."""
        pass

    @abstractmethod
    def disable_service(self) -> None:
        """Disable the systemd service."""
        pass

    @abstractmethod
    def enable_service(self) -> None:
        """Enable the systemd service."""
        pass

    @abstractmethod
    def disable_database(self) -> None:
        """Stop the workload and disable the service."""
        pass

    @abstractmethod
    def enable_database(self) -> None:
        """Enable the service and start the workload."""
        pass

    @abstractmethod
    def memory_size(self) -> int:
        """Get the total memory size of the system in Bytes.

        Returns:
            int: The total memory size in Bytes.
        """
        pass

    @abstractmethod
    def data_storage_size(self) -> int:
        """Get the size of the data storage in Bytes.

        Returns:
            int: The size of the data storage in Bytes.
        """
        pass

    @abstractmethod
    def get_db_file_size(self) -> int:
        """Get the size of the etcd database file in bytes.

        Returns:
            int: Size of the etcd database file in bytes.
        """
        pass

    @abstractmethod
    def is_lxd_cloud(self) -> bool:
        """Check if the workload is running in an LXD cloud environment.

        Returns:
            bool: True if running in LXD cloud, False otherwise.
        """
        pass

    @abstractmethod
    def data_storage_attached(self) -> bool:
        """Check if the data storage is attached.

        Returns:
            bool: True if data storage is attached, False otherwise.
        """
        pass

    def get_public_ip(self) -> str | None:
        """Get the Public IP address of the current unit."""
        cmd = ["unit-get", "public-address"]
        try:
            output = subprocess.run(
                cmd,
                check=True,
                text=True,
                shell=False,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error(f"Error executing command '{cmd}': {e}")
            return None

        if output.returncode != 0:
            return None

        return output.stdout.strip()

    def get_private_ip(self) -> str:
        """Get the Private IP address of the current unit."""
        cmd = ["unit-get", "private-address"]
        try:
            output = subprocess.run(
                cmd,
                check=True,
                text=True,
                shell=False,
                capture_output=True,
                timeout=10,
            )
            if output.returncode == 0:
                return output.stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error(f"Error executing command '{cmd}': {e}")

        return socket.gethostbyname(socket.gethostname())

    def get_host_mapping(self) -> dict[str, str]:
        """Collect hostname mapping for current unit.

        Returns:
            dict[str, str]: Dict of string keys 'hostname', 'private_ip', 'public_ip' and their values
        """
        hostname = socket.gethostname()
        private_ip = self.get_private_ip()
        public_ip = self.get_public_ip() or ""
        if not private_ip:
            raise ValueError("Could not get private IP address of the unit.")

        return {"hostname": hostname, "private_ip": private_ip, "public_ip": public_ip}

    def write_file(
        self,
        content: str,
        path: PathProtocol,
        mode: int | None = None,
        user: str | None = None,
        group: str | None = None,
    ) -> None:
        """Write the given content to the specified file path, creating parent directories if needed.

        Args:
            content (str): The content to write to the file.
            path (PathProtocol): The file path where the content will be written.
            mode (int, optional): The file mode (permissions). Defaults to None.
            user (str, optional): The user name. Defaults to None.
            group (str, optional): The group name. Defaults to None.
        """
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(content, mode=mode, user=user, group=group)

    def load_yaml_file(self, path: PathProtocol) -> Dict[str, Any]:
        """Load a YAML file from the given path.

        Args:
            path (PathProtocol): The file path to load.

        Returns:
            Dict[str, Any]: Parsed YAML content as a dictionary, or an empty dict if the file does not exist.
        """
        if not path.exists():
            return {}

        return yaml.safe_load(path.read_text()) or {}

    def load_toml_file(self, path: PathProtocol) -> Dict[str, Any]:
        """Load a TOML file from the given path.

        Args:
            path (PathProtocol): The file path to load.

        Returns:
            Dict[str, Any]: Parsed TOML content as a dictionary, or an empty dict if the file does not exist.
        """
        if not path.exists():
            return {}

        return tomllib.loads(path.read_text()) or {}

    def copy_file(self, src_file: PathProtocol, dst_file: PathProtocol) -> None:
        """Copy the contents of the source file to the destination file.

        Args:
            src_file (PathProtocol): The source file path.
            dst_file (PathProtocol): The destination file path.
        """
        dst_file.write_bytes(src_file.read_bytes())

    def remove_file(self, path: PathProtocol) -> None:
        """Remove the specified file if it exists.

        Args:
            path (PathProtocol): The file path to remove.
        """
        path.unlink(missing_ok=True)

    def remove_directory(self, directory: PathProtocol) -> None:
        """Remove a directory and all its contents.

        Args:
            directory (PathProtocol): The directory path to remove.
        """
        if not directory.exists():
            return
        self._remove_tree(directory)
        directory.rmdir()

    def _remove_tree(self, path: PathProtocol) -> None:
        """Recursively remove all files and subdirectories in the given directory.

        Args:
            path (PathProtocol): The directory path to clean.
        """
        for child in path.iterdir():
            if child.is_dir():
                self._remove_tree(child)
                child.rmdir()
            else:
                child.unlink()

    def exists(self, path: PathProtocol) -> bool:
        """Check if the given path exists and is not an empty directory.

        Args:
            path (PathProtocol): The path to check.

        Returns:
            bool: True if the path exists and is not an empty directory, False otherwise.
        """
        if path.exists():
            if path.is_dir():
                # consider it false if the directory is empty
                return len(list(path.glob("*"))) > 0
            return True

        return False
