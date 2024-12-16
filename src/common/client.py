#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""EtcdClient utility class to connect to etcd server and execute commands with etcdctl."""

import json
import logging
import subprocess
from typing import Tuple

from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdClusterManagementError,
    EtcdUserManagementError,
)
from literals import INTERNAL_USER, SNAP_NAME

logger = logging.getLogger(__name__)


class EtcdClient:
    """Handle etcd client connections and run etcdctl commands."""

    def __init__(
        self,
        username: str,
        password: str,
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

    def add_user(self, username: str) -> None:
        """Add a user to etcd."""
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
        """Run the `user passwd` command in etcd."""
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

    def add_member_as_learner(self, member_name: str, peer_url: str) -> Tuple[str, str]:
        """Add a new member as learner to the etcd-cluster.

        Returns:
            - The updated `ETCD_INITIAL_CLUSTER` to be used as config `initial-cluster` for
            starting the new cluster member
            - The `MEMBER_ID` of the newly added member, to be used for promoting the new member
            as full-voting after starting up
        """
        if result := self._run_etcdctl(
            command="member",
            subcommand="add",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            member=member_name,
            peer_url=peer_url,
            learner=True,
        ):
            # the subcommand will return the following output:
            # Member 3e23287c34b94e09 added to cluster c4d701b62779596b
            #
            # ETCD_NAME="etcd8"
            # ETCD_INITIAL_CLUSTER="etcd8=http://10.86.196.119:2380,etcd7=http://10.86.196.232:2380"
            # ETCD_INITIAL_ADVERTISE_PEER_URLS="http://10.86.196.119:2380"
            # ETCD_INITIAL_CLUSTER_STATE="existing"
            #
            # we need to parse this for the `ETCD_INITIAL_CLUSTER` configuration
            result = result.split("\n")
            logger.debug(f"Updated cluster members: {result[0]}")
            return result[3].split("ETCD_INITIAL_CLUSTER=")[1].strip('"'), result[0].split()[1]
        else:
            raise EtcdClusterManagementError(f"Failed to add {member_name} as learner.")

    def promote_member(self, member_id: str) -> None:
        """Promote a learner-member to full-voting member in the etcd-cluster."""
        if result := self._run_etcdctl(
            command="member",
            subcommand="promote",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            member=member_id,
        ):
            logger.debug(result)
        else:
            raise EtcdClusterManagementError(f"Failed to promote member {member_id}.")

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
        member: str | None = None,
        peer_url: str | None = None,
        learner: bool = False,
        output_format: str = "simple",
        use_input: str | None = None,
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
            member: member name or id, required for commands `member add/update/promote/remove`
            peer_url: url of a member to be used for cluster-internal communication
            learner: flag for adding a new cluster member as not-voting member
            output_format: set the output format (fields, json, protobuf, simple, table)
            use_input: supply text input to be passed to the `etcdctl` command (e.g. for
                        non-interactive password change)

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
            if member:
                args.append(member)
            if peer_url:
                args.append(f"--peer-urls={peer_url}")
            if learner:
                args.append("--learner=True")
            if output_format:
                args.append(f"-w={output_format}")
            if use_input:
                args.append("--interactive=False")

            result = subprocess.run(
                args=args,
                check=True,
                capture_output=True,
                text=True,
                input=use_input if use_input else "",
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
