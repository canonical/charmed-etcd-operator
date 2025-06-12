#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""EtcdClient utility class to connect to etcd server and execute commands with etcdctl."""

import json
import logging
import os
import subprocess
from typing import Tuple

import tenacity

from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdClusterManagementError,
    EtcdUserManagementError,
    HealthCheckFailedError,
)
from core.models import Member
from literals import BACKUP_FILE_NAME, INTERNAL_USER, SNAP_NAME, TLS_ROOT_DIR

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
                raise

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
            auth_username=self.user,
            auth_password=self.password,
            # only admin user is added with password, all others require `CommonName` based auth
            user_password=self.password if username == INTERNAL_USER else "",
        ):
            logger.debug(result)
        else:
            raise EtcdUserManagementError(f"Failed to add user {self.user}.")

    def get_user(self, username: str) -> dict | None:
        """Get user information from etcd.

        Args:
            username (str): The username to get information for.

        Returns:
            dict: The user information as a dictionary.
        """
        if result := self._run_etcdctl(
            command="user",
            subcommand="get",
            endpoints=self.client_url,
            user=username,
            auth_username=self.user,
            auth_password=self.password,
            output_format="json",
        ):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                raise
        return None

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
            output_format="json",
        ):
            try:
                cluster = json.loads(result)
                cluster_members = ""
                member_id = ""
                for member in cluster["members"]:
                    if member.get("name"):
                        cluster_members += f"{member['name']}={member['peerURLs'][0]},"
                    if member["peerURLs"][0] == peer_url:
                        # the member ID is returned as int, but needs to be processed as hex
                        # e.g. ID=4477466968462020105 needs to be stored as 3e23287c34b94e09
                        member_id = f"{hex(member['ID'])[2:]}"
                # for the newly added member, the member name will not be included in the response
                # we have to append it separately
                cluster_members += f"{member_name}={peer_url}"
            except (json.JSONDecodeError, KeyError):
                raise
            logger.debug(f"Updated cluster members: {cluster_members}, new member: {member_id}")
            return cluster_members, str(member_id)
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

    def remove_member(self, member_id: str) -> None:
        """Remove a cluster member."""
        if result := self._run_etcdctl(
            command="member",
            subcommand="remove",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            member=member_id,
        ):
            logger.debug(result)
        else:
            raise EtcdClusterManagementError(f"Failed to remove {member_id}.")

    def move_leader(self, new_leader_id: str) -> None:
        """Transfer Raft leadership to another etcd cluster member."""
        if result := self._run_etcdctl(
            command="move-leader",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            member=new_leader_id,
        ):
            logger.debug(result)
        else:
            raise EtcdClusterManagementError(f"Failed to transfer leadership to {new_leader_id}.")

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
        logger.debug("Health check passed.")
        return True

    def broadcast_peer_url(self, member_id: str, peer_urls: str) -> None:
        """Broadcast the peer URL to all units in the cluster.

        Args:
            member_id (str): The member ID to broadcast the peer URL for.
            peer_urls (str): The peer URLs to broadcast.
        """
        self._run_etcdctl(
            command="member",
            subcommand="update",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            member=member_id,
            peer_url=peer_urls,
        )

    def create_database_snapshot(self) -> bool:
        """Run the `snapshot save` command and return if successful."""
        if result := self._run_etcdctl(
            command="snapshot",
            subcommand="save",
            snapshot_path=BACKUP_FILE_NAME,
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
        ):
            logger.debug(result)
            return True

        return False

    def restore_database_snapshot(
        self,
        snapshot_filename: str,
        data_directory: str,
        cluster_config: str,
        peer_url: str,
        member_name: str,
    ) -> bool:
        """Run the `snapshot restore` command and return if successful."""
        return self._run_etcdutl(
            command="snapshot",
            subcommand="restore",
            snapshot_filename=snapshot_filename,
            data_directory=data_directory,
            cluster_config=cluster_config,
            peer_url=peer_url,
            member_name=member_name,
        )

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
        snapshot_path: str | None = None,
        output_format: str = "simple",
        use_input: str | None = None,
        cluster_arg: bool = False,
        prefix: str | None = None,
        role: str | None = None,
    ) -> str | None:
        """Execute `etcdctl` command via subprocess to perform online cluster admin tasks.

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
            snapshot_path: filepath for the snapshot output
            output_format: set the output format (fields, json, protobuf, simple, table)
            use_input: supply text input to be passed to the `etcdctl` command (e.g. for
                        non-interactive password change)
            cluster_arg: set to `True` if the command requires the `--cluster` argument
            prefix: prefix to be used for the `role grant-permission` command
            role: role name to be used for the `user grant-role` command

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
            if snapshot_path:
                args.append(snapshot_path)
            if output_format:
                args.append(f"-w={output_format}")
            if use_input:
                args.append("--interactive=False")
            # we append the TLS params whenever we find a client certificate
            # todo: this is not substrate-agnostic
            if os.path.exists(f"{TLS_ROOT_DIR}/client.pem"):
                args.append(f"--cert={TLS_ROOT_DIR}/client.pem")
                args.append(f"--key={TLS_ROOT_DIR}/client.key")
                args.append(f"--cacert={TLS_ROOT_DIR}/client_ca.pem")
            if cluster_arg:
                args.append("--cluster")
            if prefix:
                args.append("--prefix=true")
                args.append("readwrite")
                args.append(prefix)
            if role:
                args.append(role)

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

    def _run_etcdutl(
        self,
        command: str,
        subcommand: str | None = None,
        snapshot_filename: str | None = None,
        data_directory: str | None = None,
        cluster_config: str | None = None,
        peer_url: str | None = None,
        member_name: str | None = None,
    ) -> bool:
        """Execute `etcdutl` command via subprocess to perform offline cluster admin tasks.

        This method aims to provide a very clear interface for executing `etcdutl` and minimize
        the margin of error on cluster operations. The following arguments can be passed to the
        `etcdutl` command as parameters.

        Args:
            command: command to execute with etcdutl, e.g. `snapshot`, `defrag`
            subcommand: subcommand to add to the previous command, e.g. `restore`
            snapshot_filename: the database snapshot file to be restored
            data_directory: the directory where to store the etcd database files after restoring
            cluster_config: cluster membership configuration for the restored etcd cluster
            peer_url: url of a member to be used for cluster-internal communication after restoring
            member_name: name for this member to be set after restoring the cluster
        """
        try:
            args = [f"{SNAP_NAME}.etcdutl", command]
            if subcommand:
                args.append(subcommand)
            if snapshot_filename:
                args.append(snapshot_filename)
            if data_directory:
                args.append("--data-dir")
                args.append(data_directory)
                # by default restore snapshots with revision bump
                # see: https://etcd.io/docs/v3.6/op-guide/recovery/#restoring-with-revision-bump
                args.append("--bump-revision")
                args.append("1000000000")
                args.append("--mark-compacted")
                # allow restoring from an offline backup
                args.append("--skip-hash-check")
            if cluster_config:
                args.append("--initial-cluster")
                args.append(cluster_config)
            if peer_url:
                args.append("--initial-advertise-peer-urls")
                args.append(peer_url)
            if member_name:
                args.append("--name")
                args.append(member_name)

            # `etcdutl` commands do not return in stdout, only the return code shows if successful
            result = subprocess.run(
                args,
                check=True,
                text=True,
                capture_output=True,
                timeout=10,
            ).returncode
        except subprocess.CalledProcessError as e:
            logger.error(
                f"etcdutl {command} command failed: returncode: {e.returncode}, error: {e.stderr}"
            )
            return False
        except subprocess.TimeoutExpired as e:
            logger.error(f"Timed out running etcdutl: {e.stderr}")
            return False

        return result == 0

    def add_role(self, rolename: str) -> None:
        """Add a role to etcd.

        Args:
            rolename (str): The role name to add.
        """
        if result := self._run_etcdctl(
            command="role",
            subcommand="add",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            user=rolename,
        ):
            logger.debug(result)
        else:
            raise EtcdUserManagementError(f"Failed to add role {rolename}.")

    def grant_role(self, username: str, rolename: str) -> None:
        """Grant a role to a user in etcd.

        Args:
            username (str): The username to grant the role to.
            rolename (str): The role name to grant.
        """
        if result := self._run_etcdctl(
            command="user",
            subcommand="grant-role",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            user=username,
            role=rolename,
        ):
            logger.debug(result)
        else:
            raise EtcdUserManagementError(f"Failed to grant role {rolename} to user {username}.")

    def grant_permission(self, rolename: str, key_prefix: str) -> None:
        """Grant permission to a role in etcd.

        Args:
            rolename (str): The role name to grant permission to.
            key_prefix (str): The key prefix to grant permission for.
        """
        if result := self._run_etcdctl(
            command="role",
            subcommand="grant-permission",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            user=rolename,
            prefix=key_prefix,
        ):
            logger.debug(result)
        else:
            raise EtcdUserManagementError(f"Failed to grant permission to role {rolename}.")

    def remove_role(self, rolename: str) -> None:
        """Remove a role from etcd.

        Args:
            rolename (str): The role name to remove.
        """
        if result := self._run_etcdctl(
            command="role",
            subcommand="delete",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            user=rolename,
        ):
            logger.debug(result)
        else:
            raise EtcdUserManagementError(f"Failed to remove role {rolename}.")

    def remove_user(self, username: str) -> None:
        """Remove a user from etcd.

        Args:
            username (str): The username to remove.
        """
        if result := self._run_etcdctl(
            command="user",
            subcommand="delete",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            user=username,
        ):
            logger.debug(result)
        else:
            raise EtcdUserManagementError(f"Failed to remove user {username}.")

    def get_version(self) -> str:
        """Get the etcd version.

        Returns:
            str: The etcd version.
        """
        return self.get_endpoint_status()["Status"]["version"]

    def list_users(self) -> list[str]:
        """List users in etcd.

        Args:
            username (str): The username to get information for.

        Returns:
            list[str]: A list of users in etcd.
        """
        if result := self._run_etcdctl(
            command="user",
            subcommand="list",
            endpoints=self.client_url,
            auth_username=self.user,
            auth_password=self.password,
            output_format="json",
        ):
            return json.loads(result)["users"]
        return []
