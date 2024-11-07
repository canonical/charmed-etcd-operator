#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build the charm-under-test and deploy it together with related charms.

    Assert on the status before any relations/configurations take place.
    """
    # Build and deploy charm from local source folder
    etcd_charm = await ops_test.build_charm(".")

    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(etcd_charm, num_units=1)

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
