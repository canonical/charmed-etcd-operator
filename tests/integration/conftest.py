# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
from platform import machine

import pytest

platforms = {
    "x86_64": "amd64",
    "aarch64": "arm64",
}


@pytest.fixture(scope="package")
def arch() -> str:
    """Fixture to provide the platform architecture for testing."""
    return platforms.get(machine(), "amd64")


@pytest.fixture
def platform() -> str:
    """Fixture to provide the platform architecture for testing."""
    return platforms.get(machine(), "amd64")


@pytest.fixture
def charm(platform: str) -> str:
    """Path to the charm file to use for testing."""
    # Return str instead of pathlib.Path since python-libjuju's model.deploy(), juju deploy, and
    # juju bundle files expect local charms to begin with `./` or `/` to distinguish them from
    # Charmhub charms.
    return f"./charmed-etcd_ubuntu@24.04-{platform}.charm"