#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import pathlib
import signal
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

WRITES_LAST_WRITTEN_VAL_PATH = "last_written_value"
LOG_FILE_PATH = "log_file"
continue_running = True


def continuous_writes(endpoints: str, user: str, password: str):
    key = "cw_key"
    count = 0

    # clean up from previous runs
    pathlib.Path(WRITES_LAST_WRITTEN_VAL_PATH).unlink(missing_ok=True)
    etcd_cleanup = f"etcdctl del {key} --endpoints={endpoints} --user={user} --password={password}"
    try:
        subprocess.getoutput(etcd_cleanup)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    while continue_running:
        count += 1
        etcd_command = f"""etcdctl \
                        put {key} {count} \
                        --endpoints={endpoints} \
                        --user={user} \
                        --password={password}
                        """

        try:
            result = subprocess.getoutput(etcd_command).split("\n")
            with open(LOG_FILE_PATH, "a") as log_file:
                log_file.write(f"{result}\n")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

        time.sleep(1)
    else:
        # write last expected written value on disk when terminating
        pathlib.Path(WRITES_LAST_WRITTEN_VAL_PATH).write_text(str(count))


def handle_stop_signal(signum, frame) -> None:
    global continue_running
    continue_running = False


def main():
    endpoints = sys.argv[1]
    user = sys.argv[2]
    password = sys.argv[3]

    # handle the stop signal for a graceful stop of the writes process
    signal.signal(signal.SIGTERM, handle_stop_signal)

    continuous_writes(endpoints, user, password)


if __name__ == "__main__":
    main()
