#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess
import sys
import time

from pytest_operator.plugin import OpsTest

from literals import SNAP_NAME

from ..helpers import APP_NAME

logger = logging.getLogger(__name__)


def continuous_writes(ops_test: OpsTest, endpoints: str, user: str, password: str):
    model = ops_test.model_full_name

    key = "cw_key"
    count = 0

    while True:
        unit = ops_test.model.applications[APP_NAME].units[0]
        etcd_command = f"""{SNAP_NAME}.etcdctl \
                        put {key} {count} \
                        --endpoints={endpoints} \
                        --user={user} \
                        --password={password}
                        """
        juju_command = f"juju ssh --model={model} {unit} {etcd_command}"

        try:
            result = subprocess.getoutput(juju_command).split("\n")[0]
            logger.info(result)
            count += 1
            time.sleep(1)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(e)
            time.sleep(1)
            continue


def main():
    ops_test = sys.argv[1]
    endpoints = sys.argv[2]
    user = sys.argv[3]
    password = sys.argv[4]

    continuous_writes(ops_test, endpoints, user, password)


if __name__ == "__main__":
    main()
