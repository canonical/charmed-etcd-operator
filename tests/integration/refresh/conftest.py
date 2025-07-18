# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Pytest configuration for refresh integration tests."""

import asyncio
import logging

import pytest
import pytest_asyncio
from pytest_operator.plugin import OpsTest

# Configure logging for tests
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Refresh test specific constants
REFRESH_APP_NAME = "etcd"
REFRESH_CHARM_NAME = "charmed-etcd"
REFRESH_CHANNEL = "latest/edge"

# Test timeouts
DEPLOY_TIMEOUT = 900  # 15 minutes for deployment
IDLE_TIMEOUT = 600  # 10 minutes for idle state
ACTION_TIMEOUT = 300  # 5 minutes for actions


@pytest_asyncio.fixture(scope="session")
async def refresh_model(ops_test: OpsTest):
    """Shared model for refresh tests."""
    await ops_test.model.set_config({"logging-config": "<root>=WARNING; unit=DEBUG"})
    return ops_test.model


@pytest_asyncio.fixture(scope="session")
async def refresh_charm_app(ops_test: OpsTest, refresh_model):
    """Deploy charmed-etcd application for refresh testing."""
    logger = logging.getLogger(__name__)
    logger.info("Deploying charmed-etcd for refresh testing")

    # Deploy the charm
    await ops_test.model.deploy(
        REFRESH_CHARM_NAME,
        application_name=REFRESH_APP_NAME,
        num_units=3,
        channel=REFRESH_CHANNEL,
    )

    # Wait for deployment to complete
    await ops_test.model.wait_for_idle(
        apps=[REFRESH_APP_NAME],
        status="active",
        timeout=DEPLOY_TIMEOUT,
    )

    logger.info("Charmed-etcd deployment completed")
    return ops_test.model.applications[REFRESH_APP_NAME]


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


def pytest_configure(config):
    """Configure pytest for refresh tests."""
    config.addinivalue_line("markers", "refresh: mark test as a refresh functionality test")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "unit: mark test as unit test")
    config.addinivalue_line("markers", "integration: mark test as integration test")


def pytest_collection_modifyitems(config, items):
    """Modify test collection for refresh tests."""
    for item in items:
        # Mark all tests in refresh directory as refresh tests
        if "refresh" in str(item.fspath):
            item.add_marker(pytest.mark.refresh)

        # Mark integration tests
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)

        # Mark slow tests
        if any(
            keyword in item.name.lower() for keyword in ["advanced", "performance", "scalability"]
        ):
            item.add_marker(pytest.mark.slow)
