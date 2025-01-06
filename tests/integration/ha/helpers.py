#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import subprocess

from pytest_operator.plugin import OpsTest


async def existing_app(ops_test: OpsTest) -> str | None:
    """Return the name of an existing etcd cluster."""
    apps = json.loads(
        subprocess.check_output(
            f"juju status --model {ops_test.model.info.name} --format=json".split()
        )
    )["applications"]

    etcd_apps = {name: desc for name, desc in apps.items() if desc["charm-name"] == "charmed-etcd"}

    return list(etcd_apps.keys())[0] if etcd_apps else None
