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
from dataclasses import dataclass, field
from typing import Any, Dict, List

from literals import CONFIG_FILE, TLS_ROOT_DIR

logger = logging.getLogger(__name__)


@dataclass
class TLSPaths:
    """Paths for TLS."""

    root_dir: str = TLS_ROOT_DIR

    @property
    def peer_ca(self) -> str:
        """Path to the peer CA."""
        return f"{self.root_dir}/peer_ca.pem"

    @property
    def peer_cert(self) -> str:
        """Path to the peer cert."""
        return f"{self.root_dir}/peer.pem"

    @property
    def peer_key(self) -> str:
        """Path to the peer key."""
        return f"{self.root_dir}/peer.key"

    @property
    def client_ca(self) -> str:
        """Path to the client CA."""
        return f"{self.root_dir}/client_ca.pem"

    @property
    def client_cert(self) -> str:
        """Path to the server cert."""
        return f"{self.root_dir}/client.pem"

    @property
    def client_key(self) -> str:
        """Path to the server key."""
        return f"{self.root_dir}/client.key"

    @property
    def backup_ca(self) -> str:
        """Path to the CA for backup/restore object storage."""
        return f"{self.root_dir}/backup_ca.pem"


@dataclass
class EtcdPaths:
    """Paths for etcd."""

    config_file: str = CONFIG_FILE
    tls: TLSPaths = field(default_factory=TLSPaths)


class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    paths: EtcdPaths = EtcdPaths()

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
    def write_file(self, content: str, file: str) -> None:
        """Write content to a file.

        Args:
            content (str): Content to write to the file.
            file (str): Path to the file.
        """
        pass

    @abstractmethod
    def load_yaml_file(self, file: str) -> Dict[str, Any]:
        """Read yaml content from a file.

        Args:
            file (str): Path to the file.

        Returns:
            The content of a YAML file as a dict.
        """
        pass

    @abstractmethod
    def load_toml_file(self, file: str) -> Dict[str, Any]:
        """Read toml content from a file.

        Args:
            file (str): Path to the file.

        Returns:
            The content of a TOML file as a dict.
        """
        pass

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
    def copy_file(self, src_file: str, dst_file: str) -> None:
        """Copy a source-file to a destination-file."""
        pass

    @abstractmethod
    def remove_file(self, file: str) -> None:
        """Remove a file.

        Args:
            file (str): Path to the file.
        """
        pass

    @abstractmethod
    def remove_directory(self, directory: str) -> None:
        """Remove a directory.

        Args:
            directory (str): Path to the directory.
        """
        pass

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a file or directory exists.

        Args:
            path (str): Path to the file or directory.

        Returns:
            bool: True if the file or directory exists, False otherwise.
        """
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

    def get_public_ip(self) -> str | None:
        """Get the Public IP address of the current unit."""
        cmd = "unit-get public-address"
        try:
            output = subprocess.run(
                cmd,
                check=True,
                text=True,
                shell=True,
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
        cmd = "unit-get private-address"
        try:
            output = subprocess.run(
                cmd,
                check=True,
                text=True,
                shell=True,
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
