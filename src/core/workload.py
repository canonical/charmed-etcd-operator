#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base objects for workload operations across different substrates."""

import secrets
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from literals import CONFIG_FILE, TLS_ROOT_DIR


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
