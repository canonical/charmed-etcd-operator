#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""EtcdClient utility class to connect to etcd server and execute commands with etcdctl."""

import json
import logging
import subprocess

import tenacity

from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdUserManagementError,
    HealthCheckFailedError,
)
from core.models import Member
from literals import INTERNAL_USER, SNAP_NAME, TLS_ROOT_DIR

logger = logging.getLogger(__name__)


class EtcdClient:
    """Handle etcd client connections and run etcdctl commands."""

    def __init__(
        self,
        username: str,
        password: str,
        client_url: str,
        tls_path: str | None = None,
    ):
        self.client_url = client_url
        self.user = username
        self.password = password
        self.tls_path = tls_path

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

    def add_user(self, username: str) -> None:
        """Add a user to etcd.

        Args:
            username (str): The username to add.
        """
        if result := self._run_etcdctl(
            command="user",
            subcommand="add",
            endpoints=self.client_url,
            user=username,
            # only admin user is added with password, all others require `CommonName` based auth
            user_password=self.password if username == INTERNAL_USER else "",
        ):
            logger.debug(result)
        else:
            raise EtcdUserManagementError(f"Failed to add user {self.user}.")

    def update_password(self, username: str, new_password: str) -> None:
        """Run the `user passwd` command in etcd.

        Args:
            username (str): The username to update the password for.
            new_password (str): The new password to set.
        """
        if result := self._run_etcdctl(
            command="user",
            subcommand="passwd",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            user=username,
            use_input=new_password,
        ):
            logger.debug(f"{result} for user {username}.")
        else:
            raise EtcdUserManagementError(f"Failed to update user {username}.")

    def enable_auth(self) -> None:
        """Enable authentication in etcd."""
        if result := self._run_etcdctl(
            command="auth",
            subcommand="enable",
            endpoints=self.client_url,
        ):
            logger.debug(result)
        else:
            raise EtcdAuthNotEnabledError("Failed to enable authentication in etcd.")

    def _run_etcdctl(  # noqa: C901
        self,
        command: str,
        endpoints: str,
        subcommand: str | None = None,
        # We need to be able to run `etcdctl` without user/pw
        # otherwise it will error if auth is not yet enabled
        # this is relevant for `user add` and `auth enable` commands
        auth_username: str | None = None,
        auth_password: str | None = None,
        user: str | None = None,
        user_password: str | None = None,
        output_format: str = "simple",
        use_input: str | None = None,
        member_id: str | None = None,
        peer_urls: str | None = None,
        cluster_arg: bool = False,
    ) -> str | None:
        """Execute `etcdctl` command via subprocess.

        This method aims to provide a very clear interface for executing `etcdctl` and minimize
        the margin of error on cluster operations. The following arguments can be passed to the
        `etcdctl` command as parameters.

        Args:
            command: command to execute with etcdctl, e.g. `elect`, `member` or `endpoint`
            subcommand: subcommand to add to the previous command, e.g. `add` or `status`
            endpoints: str-formatted list of endpoints to run the command against
            auth_username: username used for authentication
            auth_password: password used for authentication
            user: username to be added or updated in etcd
            user_password: password to be set for the user that is added to etcd
            output_format: set the output format (fields, json, protobuf, simple, table)
            use_input: supply text input to be passed to the `etcdctl` command (e.g. for
                        non-interactive password change)
            member_id: member ID to be used in the command
            peer_urls: peer URLs to be used in the command
            cluster_arg: set to `True` if the command requires the `--cluster` argument

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
            if member_id:
                args.append(member_id)
            if user:
                args.append(user)
            if user_password == "":
                args.append("--no-password=True")
            elif user_password:
                args.append(f"--new-user-password={user_password}")
            if endpoints:
                args.append(f"--endpoints={endpoints}")
            if auth_username:
                args.append(f"--user={auth_username}")
            if auth_password:
                args.append(f"--password={auth_password}")
            if output_format:
                args.append(f"-w={output_format}")
            if use_input:
                args.append("--interactive=False")
            if peer_urls:
                args.append(f"--peer-urls={peer_urls}")
            if "https" in endpoints:
                args.append(f"--cert={TLS_ROOT_DIR}/client.pem")
                args.append(f"--key={TLS_ROOT_DIR}/client.key")
                args.append(f"--cacert={TLS_ROOT_DIR}/client_ca.pem")
            if cluster_arg:
                args.append("--cluster")

            result = subprocess.run(
                args,
                check=True,
                text=True,
                capture_output=True,
                input=use_input,
                timeout=10,
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

    def member_list(self) -> dict[str, Member] | None:
        """Run the `member list` command in etcd.

        Returns:
            dict[str, MemberListResult]: A dictionary with the member name as key and the
            MemberListResult as value.
        """
        result = self._run_etcdctl(
            command="member",
            subcommand="list",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            output_format="json",
        )
        logger.debug(f"Member list: {result}")
        if not result:
            return None

        result = json.loads(result)
        return {
            member["name"]: Member(
                id=str(hex(member["ID"]))[2:],
                name=member["name"],
                peer_urls=member["peerURLs"],
                client_urls=member["clientURLs"],
            )
            for member in result["members"]
        }

    def is_healthy(self, cluster: bool = False) -> bool:
        """Run the `endpoint health` command and return True if healthy.

        Args:
            cluster (bool): set to `True` to check the health of the entire cluster.

        Returns:
            bool: True if the cluster or node is healthy.
        """
        logger.debug("Running etcd health check.")
        try:
            for attempt in tenacity.Retrying(
                stop=tenacity.stop_after_attempt(5),
                wait=tenacity.wait_fixed(5),
                reraise=True,
            ):
                with attempt:
                    logger.debug(
                        f"Checking health for cluster attempt: {attempt.retry_state.attempt_number}"
                    )
                    result = self._run_etcdctl(
                        auth_password=self.password,
                        auth_username=self.user,
                        command="endpoint",
                        subcommand="health",
                        endpoints=self.client_url,
                        output_format="json",
                        cluster_arg=cluster,
                    )

                    if result is None:
                        raise HealthCheckFailedError("Health check command failed")

                    for endpoint in json.loads(result):
                        if not endpoint["health"]:
                            raise HealthCheckFailedError(
                                f"Health check failed for endpoint {endpoint['endpoint']}"
                            )

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
        return True

    def broadcast_peer_url(self, endpoints: str, member_id: str, peer_urls: str) -> None:
        """Broadcast the peer URL to all units in the cluster.

        Args:
            endpoints (str): The endpoints to run the command against.
            member_id (str): The member ID to broadcast the peer URL for.
            peer_urls (str): The peer URLs to broadcast.
        """
        self._run_etcdctl(
            command="member",
            subcommand="update",
            endpoints=endpoints,
            auth_username=self.user,
            auth_password=self.password,
            member_id=member_id,
            peer_urls=peer_urls,
        )
