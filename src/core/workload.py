#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base objects for workload operations across different substrates."""

from abc import ABC, abstractmethod


class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    @abstractmethod
    def start(self) -> None:
        """Start the workload service."""
        pass
