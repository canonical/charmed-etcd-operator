#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""EtcdClient utility class to connect to etcd server and execute commands with etcdctl."""

import json
import logging
import subprocess

from literals import SNAP_NAME

logger = logging.getLogger(__name__)


class EtcdClient:
    """Handle etcd client connections and run etcdctl commands."""

    def __init__(
        self,
        client_url: str,
    ):
        self.client_url = client_url

    def get_endpoint_status(self) -> dict:
        """Run the `endpoint status` command and return the result as dict."""
        endpoint_status = {}
        if result := self._run_etcdctl(
            command="endpoint",
            subcommand="status",
            endpoints=self.client_url,
            output_format="json",
        ):
            endpoint_status = json.loads(result)[0]

        return endpoint_status

    def _run_etcdctl(
        self,
        command: str,
        subcommand: str | None,
        endpoints: str,
        output_format: str | None = "simple",
    ) -> str | None:
        """Execute `etcdctl` command via subprocess.

        The list of arguments will be extended once authentication/encryption is implemented.
        This method aims to provide a very clear interface for executing `etcdctl` and minimize
        the margin of error on cluster operations.

        Args:
            command: command to execute with etcdctl, e.g. `elect`, `member` or `endpoint`
            subcommand: subcommand to add to the previous command, e.g. `add` or `status`
            endpoints: str-formatted list of endpoints to run the command against
            output_format: set the output format (fields, json, protobuf, simple, table)
            ...

        Returns:
            The output of the subprocess-command as a string. In case of error, this will
            return `None`. It will not raise an error in order to leave error handling up
            to the caller. Depending on what command is executed, the ways of handling errors
            might differ.
        """
        try:
            result = subprocess.run(
                args=[
                    f"{SNAP_NAME}.etcdctl",
                    command,
                    subcommand,
                    f"--endpoints={endpoints}",
                    f"-w={output_format}",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.warning(e)
            return None

        return result
