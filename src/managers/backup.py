#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all backup/restore related tasks."""

import logging
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

from common.client import EtcdClient
from common.exceptions import EtcdBackupError
from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import BACKUP_FILE_PATH, BACKUP_ID_FORMAT, INTERNAL_USER

logger = logging.getLogger(__name__)


class BackupManager:
    """Manage everything related to backup and restore."""

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload
        self.admin_user = INTERNAL_USER
        self.admin_password = self.state.cluster.internal_user_credentials.get(INTERNAL_USER, "")

    def create_bucket(self, s3_parameters: dict[str, str]) -> None:
        """Create bucket if it does not exist yet."""
        bucket_name = s3_parameters["bucket"]
        region = s3_parameters.get("region")
        s3_resource = boto3.resource(
            "s3",
            region_name=region,
            endpoint_url=s3_parameters["endpoint"],
            aws_access_key_id=s3_parameters["access-key"],
            aws_secret_access_key=s3_parameters["secret-key"],
            verify=self.workload.paths.tls.backup_ca
            if s3_parameters.get("tls-ca-chain")
            else True,
        )

        bucket = s3_resource.Bucket(bucket_name)
        try:
            if region:
                bucket.create(CreateBucketConfiguration={"LocationConstraint": region})
            else:
                bucket.create()
            bucket.wait_until_exists()
        except ClientError as e:
            if "BucketAlreadyOwnedByYou" in e.args[0] or "BucketAlreadyExists" in e.args[0]:
                logger.info(f"Using existing bucket {bucket_name}")
                return
            else:
                # todo: do we want to raise to make the user aware of the error?
                raise

        logger.info(f"Created bucket {bucket_name}")

    def create_backup(self) -> str:
        """Create a backup of etcd and upload it to object storage.

        Returns:
            str: the backup_id uploaded to object storage
        """
        backup_id = datetime.now().strftime(BACKUP_ID_FORMAT)
        s3_parameters = self.state.cluster.s3_credentials
        upload_target = f"{s3_parameters['path']}/{backup_id}"

        etcd_client = EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

        if not etcd_client.create_database_snapshot():
            raise EtcdBackupError("Failed to create database backup.")

        s3_resource = boto3.resource(
            "s3",
            region_name=s3_parameters.get("region"),
            endpoint_url=s3_parameters["endpoint"],
            aws_access_key_id=s3_parameters["access-key"],
            aws_secret_access_key=s3_parameters["secret-key"],
            verify=self.workload.paths.tls.backup_ca
            if s3_parameters.get("tls-ca-chain")
            else True,
        )
        bucket = s3_resource.Bucket(s3_parameters["bucket"])

        try:
            bucket.upload_file(BACKUP_FILE_PATH, upload_target)
        except ClientError as e:
            raise EtcdBackupError(e)

        logger.info(f"Backup uploaded to {upload_target}")

        self.workload.remove_file(BACKUP_FILE_PATH)
        logger.debug(f"Removed temporary snapshot file {BACKUP_FILE_PATH}")

        return backup_id

    def list_backups(self) -> list:
        """Get the list of available backups in the configured object storage."""
        s3_parameters = self.state.cluster.s3_credentials

        s3_resource = boto3.resource(
            "s3",
            region_name=s3_parameters.get("region"),
            endpoint_url=s3_parameters["endpoint"],
            aws_access_key_id=s3_parameters["access-key"],
            aws_secret_access_key=s3_parameters["secret-key"],
            verify=self.workload.paths.tls.backup_ca
            if s3_parameters.get("tls-ca-chain")
            else True,
        )
        bucket = s3_resource.Bucket(s3_parameters["bucket"])
        backup_list = []

        try:
            bucket_objects = bucket.objects.filter(Prefix=s3_parameters["path"])
            for bucket_object in bucket_objects:
                backup_list.append(bucket_object.key)
        except ClientError as e:
            raise EtcdBackupError(e)

        # current format: ['etcd-backups/2025-03-19T11:56:30Z','etcd-backups/2025-03-19T11:57:52Z']
        backup_list = [b.replace(f"{s3_parameters['path']}/", "") for b in backup_list]
        backup_list.sort(reverse=True)

        return backup_list

    @staticmethod
    def format_backup_list(backup_list: list[str]) -> str:
        """Format a list of backup_id's as a table and return the output."""
        output = [f"{'backup-id':<21} | backup-status"]

        output.append("-" * len(output[0]))
        for backup_id in backup_list:
            output.append(f"{backup_id:<21} | finished")

        return "\n".join(output)

    def store_tls_ca_chain(self, s3_parameters: dict[str, str]) -> None:
        """Write the TLS CA chain provided by s3-integrator to the filesystem."""
        if not (tls_ca_chain := s3_parameters.get("tls-ca-chain")):
            return

        raw_ca = "\n".join(cert for cert in tls_ca_chain)
        self.workload.write_file(raw_ca, self.workload.paths.tls.backup_ca)
        logger.debug(f"TLS CA chain stored in {self.workload.paths.tls.backup_ca}")
