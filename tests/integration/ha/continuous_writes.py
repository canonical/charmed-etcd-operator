#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

WRITES_LAST_WRITTEN_VAL_PATH = "last_written_value"


def continuous_writes(endpoints: str, user: str, password: str):
    key = "cw_key"
    count = 0

    while True:
        count += 1
        etcd_command = f"""etcdctl \
                        put {key} {count} \
                        --endpoints={endpoints} \
                        --user={user} \
                        --password={password}
                        """

        try:
            result = subprocess.getoutput(etcd_command).split("\n")[0]
            logger.info(result)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

        # write last expected written value on disk
        with open(WRITES_LAST_WRITTEN_VAL_PATH, "w") as f:
            f.write(str(count))
            os.fsync(f)
        time.sleep(1)


def main():
    endpoints = sys.argv[1]
    user = sys.argv[2]
    password = sys.argv[3]

    continuous_writes(endpoints, user, password)


if __name__ == "__main__":
    main()
