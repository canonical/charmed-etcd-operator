#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base objects for workload operations across different substrates."""

import secrets
import string
from abc import ABC, abstractmethod


class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    @abstractmethod
    def start(self) -> None:
        """Start the workload service."""
        pass

    @abstractmethod
    def alive(self) -> bool:
        """Check if the workload is running."""
        pass

    @abstractmethod
    def write_file(self, content: str, file: str) -> None:
        """Write content to a file."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the workload service."""
        pass

    @staticmethod
    def generate_password() -> str:
        """Create randomized string for use as app passwords.

        Returns:
            String of 32 randomized letter+digit characters
        """
        return "".join([secrets.choice(string.ascii_letters + string.digits) for _ in range(32)])
