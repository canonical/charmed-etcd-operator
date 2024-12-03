#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""EtcdClient utility class to connect to etcd server and execute commands with etcdctl."""

import json
import logging
import subprocess
from typing import Optional

from common.exceptions import EtcdAuthNotEnabledError, EtcdUserNotCreatedError
from literals import INTERNAL_USER, SNAP_NAME

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
            output_format="json",
        ):
            try:
                endpoint_status = json.loads(result)[0]
            except json.JSONDecodeError:
                pass

        return endpoint_status

    def add_user(self, username: str):
        """Add a user to etcd."""
        if result := self._run_etcdctl(
            command="user",
            subcommand="add",
            endpoints=self.client_url,
            new_user=username,
            # only admin user is added with password, all others require `CommonName` based auth
            new_user_password=self.password if username == INTERNAL_USER else "",
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
        # We need to be able to run `etcdctl` without user/pw
        # otherwise it will error if auth is not yet enabled
        # this is relevant for `user add` and `auth enable` commands
        username: Optional[str] = None,
        password: Optional[str] = None,
        new_user: Optional[str] = None,
        new_user_password: Optional[str] = None,
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
            new_user: username to be added to etcd
            new_user_password: password to be set for the new user
            output_format: set the output format (fields, json, protobuf, simple, table)
            ...

        Returns:
            The output of the subprocess-command as a string. In case of error, this will
            return `None`. It will not raise an error in order to leave error handling up
            to the caller. Depending on what command is executed, the ways of handling errors
            might differ.
        """
        try:
            args = [f"{SNAP_NAME}.etcdctl", command]
            if subcommand:
                args.append(subcommand)
            if new_user:
                args.append(new_user)
            if new_user_password == "":
                args.append("--no-password=True")
            elif new_user_password:
                args.append(f"--new-user-password={new_user_password}")
            if endpoints:
                args.append(f"--endpoints={endpoints}")
            if username:
                args.append(f"--user={username}")
            if password:
                args.append(f"--password={password}")
            if output_format:
                args.append(f"-w={output_format}")

            result = subprocess.run(
                args=args,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(
                f"etcdctl {command} command failed: returncode: {e.returncode}, error: {e.stderr}"
            )
            return None
        except subprocess.TimeoutExpired as e:
            logger.error(f"Timed out running etcdctl: {e.stderr}")
            return None

        return result
