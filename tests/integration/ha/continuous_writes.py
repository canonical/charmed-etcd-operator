#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import subprocess
import sys
import time

from literals import SNAP_NAME

logger = logging.getLogger(__name__)


def continuous_writes(model: str, unit: str, endpoints: str, user: str, password: str):
    key = "cw_key"
    count = 0

    while True:
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
    model = sys.argv[1]
    unit = sys.argv[2]
    endpoints = sys.argv[3]
    user = sys.argv[4]
    password = sys.argv[5]

    continuous_writes(model, unit, endpoints, user, password)


if __name__ == "__main__":
    main()
