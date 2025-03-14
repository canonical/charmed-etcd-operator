#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all backup/restore related tasks."""

import logging

import boto3
from botocore.exceptions import ClientError

# from common.client import EtcdClient
from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import INTERNAL_USER

logger = logging.getLogger(__name__)


class BackupManager:
    """Manage everything related to backup and restore."""

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload
        self.admin_user = INTERNAL_USER
        self.admin_password = self.state.cluster.internal_user_credentials.get(INTERNAL_USER, "")
        self.cluster_endpoints = [server.client_url for server in self.state.servers]

    def create_bucket(self, s3_parameters: dict[str, str]) -> None:
        """Create bucket if it does not exist yet."""
        logger.info(f"s3 parameters: {s3_parameters}")
        bucket_name = s3_parameters["bucket"]
        region = s3_parameters["region"]
        s3_client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=s3_parameters["endpoint"],
            aws_access_key_id=s3_parameters["access-key"],
            aws_secret_access_key=s3_parameters["secret-key"],
        )

        try:
            if region:
                location = {"LocationConstraint": region}
                s3_client.create_bucket(Bucket=bucket_name, CreateBucketConfiguration=location)
            else:
                s3_client.create_bucket(Bucket=bucket_name)
        except ClientError:
            # todo: do we want to raise to make the user aware of the error?
            raise

        logger.info(f"Created bucket {bucket_name}")
