#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all cluster/quorum/rbac related tasks."""

import logging
import socket
from json import JSONDecodeError

from tenacity import retry, stop_after_attempt, wait_random_exponential

from common.client import EtcdClient
from common.exceptions import (
    EtcdAuthNotEnabledError,
    EtcdClusterManagementError,
    EtcdUserManagementError,
    RaftLeaderNotFoundError,
)
from core.cluster import ClusterState
from core.models import Member
from core.workload import WorkloadBase
from literals import INTERNAL_USER, EtcdClusterState, TLSState

logger = logging.getLogger(__name__)


class ClusterManager:
    """Manage cluster members, quorum and authorization."""

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload
        self.admin_user = INTERNAL_USER
        self.admin_password = self.state.cluster.internal_user_credentials.get(INTERNAL_USER, "")
        self.cluster_endpoints = [server.client_url for server in self.state.servers]

    def get_host_mapping(self) -> dict[str, str]:
        """Collect hostname mapping for current unit.

        Returns:
            dict[str, str]: Dict of string keys 'hostname', 'ip' and their values
        """
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)

        return {"hostname": hostname, "ip": ip}

    @property
    def leader(self) -> str:
        """Query the etcd cluster for the raft leader.

        Returns:
            str: The member id of the raft leader in hex representation.
        """
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=self.state.unit_server.client_url,
            )
            endpoint_status = client.get_endpoint_status()
            leader_id = endpoint_status["Status"]["leader"]
            # the leader ID is returned as int, but needs to be processed as hex
            # e.g. ID=4477466968462020105 needs to be returned as 3e23287c34b94e09
            return hex(leader_id)[2:]
        except (KeyError, JSONDecodeError) as e:
            raise RaftLeaderNotFoundError(f"No raft leader found: {e}")

    def enable_authentication(self) -> None:
        """Enable the etcd admin user and authentication."""
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=self.state.unit_server.client_url,
            )
            client.add_user(username=self.admin_user)
            client.enable_auth()
        except (EtcdAuthNotEnabledError, EtcdUserManagementError):
            raise

    def update_credentials(self, username: str, password: str) -> None:
        """Update a user's password.

        Args:
            username (str): The username to update.
            password (str): The new password.
        """
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=self.state.unit_server.client_url,
            )
            client.update_password(username=username, new_password=password)
        except EtcdUserManagementError:
            raise

    @property
    def member(self) -> Member:
        """Get the member information of the current unit.

        Returns:
            Member: The member object.
        """
        logger.debug(f"Getting member for unit {self.state.unit_server.member_name}")
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

        member_list = client.member_list()
        if member_list is None:
            raise ValueError("member list command failed")
        if self.state.unit_server.member_name not in member_list:
            raise ValueError("member name not found")

        logger.debug(f"Member: {member_list[self.state.unit_server.member_name].id}")
        return member_list[self.state.unit_server.member_name]

    def broadcast_peer_url(self, peer_urls: str) -> None:
        """Broadcast the peer URL to all units in the cluster.

        Args:
            peer_urls (str): The peer URLs to broadcast.
        """
        logger.debug(
            f"Broadcasting peer URL: {peer_urls} for unit {self.state.unit_server.member_name}"
        )
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=",".join(e for e in self.cluster_endpoints),
        )
        client.broadcast_peer_url(self.state.unit_server.client_url, self.member.id, peer_urls)

    def is_healthy(self, cluster=True) -> bool:
        """Run the `endpoint health` command and return True if healthy.

        Args:
            cluster (bool): True if the health check should be cluster-wide.

        Returns:
                bool: True if the cluster or node is healthy.
        """
        if not self.admin_password:
            self.admin_password = self.state.cluster.internal_user_credentials.get(
                INTERNAL_USER, ""
            )

        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.is_healthy(cluster=cluster)

    def restart_member(self) -> bool:
        """Restart the workload.

        Returns:
            bool: True if the workload is running after restart.
        """
        logger.debug("Restarting workload")
        self.workload.restart()
        return self.is_healthy(cluster=False)

    def add_member(self, unit_name: str) -> None:
        """Add a new member to the etcd cluster."""
        # retrieve the member information for the newly joined unit from the set of EtcdServers
        server = next(iter([s for s in self.state.servers if s.unit_name == unit_name]), None)
        if not server:
            raise KeyError(f"Peer relation data for unit {unit_name} not found.")

        # we need to make sure all required information are available before adding the member
        if server.member_name and server.ip and server.peer_url:
            peer_url = server.peer_url
            # When the peer relation joined event is triggered, the peer_url is in http:// format
            # because the node would still not have gotten its certificates
            if self.state.unit_server.tls_peer_state == TLSState.TLS:
                peer_url = peer_url.replace("http://", "https://")
            try:
                client = EtcdClient(
                    username=self.admin_user,
                    password=self.admin_password,
                    client_url=",".join(e for e in self.cluster_endpoints),
                )
                cluster_members, member_id = client.add_member_as_learner(
                    server.member_name, peer_url
                )
                self.state.cluster.update(
                    {"cluster_members": cluster_members, "learning_member": member_id}
                )
                logger.info(f"Added unit {unit_name} as new cluster member {member_id}.")
            except (EtcdClusterManagementError, JSONDecodeError):
                raise
        else:
            raise KeyError(f"Peer relation data for unit {unit_name} not found.")

    def start_member(self) -> None:
        """Start a cluster member and update its status."""
        self.workload.start()
        # this triggers a relation_changed event which the leader will use to promote
        # a learner-member to fully-voting member
        self.state.unit_server.update({"state": "started"})
        if not self.state.cluster.cluster_state:
            # mark the cluster as initialized
            self.state.cluster.update(
                {
                    "cluster_state": EtcdClusterState.EXISTING.value,
                    "cluster_members": self.state.unit_server.member_endpoint,
                }
            )

    def promote_learning_member(self) -> None:
        """Promote a learning member to full-voting member."""
        member_id = self.state.cluster.learning_member

        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=",".join(e for e in self.cluster_endpoints),
            )
            client.promote_member(member_id=member_id)
        except EtcdClusterManagementError:
            raise

        self.state.cluster.update({"learning_member": ""})
        logger.info(f"Successfully promoted learning member {member_id}.")

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_random_exponential(multiplier=2, max=60),
        reraise=True,
    )
    def remove_member(self) -> None:
        """Remove a cluster member and stop the workload."""
        try:
            client = EtcdClient(
                username=self.admin_user,
                password=self.admin_password,
                client_url=",".join(e for e in self.cluster_endpoints),
            )
            if self.member.id == self.leader:
                new_leader_id = self.select_new_leader()
                logger.debug(f"Next selected leader: {new_leader_id}")
                client.move_leader(new_leader_id)
            # by querying the member's id we make sure the cluster is available with quorum
            # otherwise we raise and retry
            client.remove_member(self.member.id)
        except (EtcdClusterManagementError, RaftLeaderNotFoundError, ValueError):
            raise

    def select_new_leader(self) -> str:
        """Choose a new leader from the current cluster members.

        Returns:
            str: The member id of the next cluster member in hex representation.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

        member_list = client.member_list()
        if member_list is None:
            raise ValueError("member list command failed")
        member_list.pop(self.state.unit_server.member_name, None)
        return next(iter(member_list.values())).id

    def get_user(self, username: str) -> dict | None:
        """Get the user information.

        Args:
            username (str): The username to get.

        Returns:
            (dict | None): The user information or None if the user does not exist.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.get_user(username=username)

    def get_role(self, rolename: str) -> list[dict] | None:
        """Get the role information.

        Args:
            rolename (str): The name of the role to get.

        Returns:
            list[dict] | None: The role's list of permissions or None if the role does not exist.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.get_role(rolename)

    def add_role(self, rolename: str) -> None:
        """Add a new role.

        Args:
            rolename (str): The name of the role to add.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client.add_role(rolename)

    def grant_role(self, username: str, rolename: str) -> None:
        """Grant a role to a user.

        Args:
            username (str): The name of the user.
            rolename (str): The name of the role.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client.grant_role(username, rolename)

    def grant_permission(self, rolename: str, prefix: str) -> None:
        """Grant permission to a role.

        Args:
            rolename (str): The name of the role.
            prefix (str): The prefix to grant permission to.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client.grant_permission(rolename, prefix)

    def add_user(self, username: str) -> None:
        """Add a new user.

        Args:
            username (str): The name of the user to add.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client.add_user(username)

    def remove_role(self, rolename: str) -> None:
        """Remove a role.

        Args:
            rolename (str): The name of the role to remove.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client.remove_role(rolename)

    def remove_user(self, username: str) -> None:
        """Remove a user.

        Args:
            username (str): The name of the user to remove.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        client.remove_user(username)

    def get_version(self) -> str:
        """Get the etcd version.

        Returns:
            str: The etcd version.
        """
        client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )
        return client.get_version()

    def remove_managed_user(self, username: str):
        """Remove user and role from the cluster.

        Args:
            username (str): The name of the user to remove.
        """
        self.remove_role(username)
        self.remove_user(username)
        logger.info(f"Removed managed user {username}")

    def add_managed_user(self, username: str, keys_prefix: str):
        """Add user and role to the cluster.

        Args:
            username (str): The name of the user to add.
            keys_prefix (str): The keys prefix to grant permission to.
        """
        self.add_user(username)
        self.add_role(username)
        self.grant_role(username, username)
        self.grant_permission(username, keys_prefix)
        logger.info(f"Added managed user {username}")
