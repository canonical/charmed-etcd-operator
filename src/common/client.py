#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""EtcdClient utility class to connect to etcd server and execute commands with etcdctl."""

import json
import logging
import subprocess
from typing import Optional

from common.exceptions import EtcdAuthNotEnabledError, EtcdUserNotCreatedError

logger = logging.getLogger(__name__)


class EtcdClient:
    """Handle etcd client connections and run etcdctl commands."""

    def __init__(
        self,
        username,
        password,
        client_url: str,
    ):
        self.client_url = client_url
        self.user = username
        self.password = password

    def get_endpoint_status(self) -> dict:
        """Run the `endpoint status` command and return the result as dict."""
        endpoint_status = {}
        if result := self._run_etcdctl(
            command="endpoint",
            subcommand="status",
            endpoints=self.client_url,
            username=self.user,
            password=self.password,
            output_format="json",
        ):
            try:
                endpoint_status = json.loads(result)[0]
            except json.JSONDecodeError:
                pass

        return endpoint_status

    def add_admin_user(self):
        """Add the internal admin with password. Raise if not successful."""
        if result := self._run_etcdctl(
            command="user",
            subcommand="add",
            endpoints=self.client_url,
            new_user=f"{self.user}:{self.password}",
        ):
            logger.debug(result)
        else:
            raise EtcdUserNotCreatedError(f"Failed to add user {self.user}.")

    def add_client_user(self):
        """Add non-admin user with `CommonName` based authentication`."""
        if result := self._run_etcdctl(
            command="user",
            subcommand="add",
            endpoints=self.client_url,
            new_user=self.user,
            no_password=True,
        ):
            logger.debug(result)
        else:
            raise EtcdUserNotCreatedError(f"Failed to add user {self.user}.")

    def enable_auth(self):
        """Enable authentication in etcd."""
        if result := self._run_etcdctl(
            command="auth",
            subcommand="enable",
            endpoints=self.client_url,
        ):
            logger.debug(result)
        else:
            raise EtcdAuthNotEnabledError("Failed to enable authentication in etcd.")

    def _run_etcdctl(
        self,
        command: str,
        endpoints: str,
        subcommand: Optional[str] = None,
        # We need to be able to run `etcdctl` with empty user/pw
        # otherwise it will error if auth is not yet enabled
        # this is relevant for `user add` and `auth enable` commands
        username: Optional[str] = None,
        password: Optional[str] = None,
        new_user: Optional[str] = None,
        no_password: Optional[bool] = False,
        output_format: Optional[str] = "simple",
    ) -> str | None:
        """Execute `etcdctl` command via subprocess.

        The list of arguments will be extended once authentication/encryption is implemented.
        This method aims to provide a very clear interface for executing `etcdctl` and minimize
        the margin of error on cluster operations.

        Args:
            command: command to execute with etcdctl, e.g. `elect`, `member` or `endpoint`
            subcommand: subcommand to add to the previous command, e.g. `add` or `status`
            endpoints: str-formatted list of endpoints to run the command against
            username: user for authentication
            password: password for authentication
            new_user: username (and password, in case of admin user) to be added to etcd
            no_password: add a new user with the --no-password option for CN based authentication
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
                    "etcdctl",
                    command,
                    subcommand if subcommand else "",
                    new_user if new_user else "",
                    f"--endpoints={endpoints}",
                    f"--user={username}" if username else "",
                    f"--password={password}" if password else "",
                    "--no-password" if no_password else "",
                    f"-w={output_format}",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except subprocess.CalledProcessError:
            logger.warning(f"etcdctl {command} command failed.")
            return None

        return result
