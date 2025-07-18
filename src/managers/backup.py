#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for all backup/restore related tasks."""

import logging
from datetime import datetime

import boto3
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContainerClient
from botocore.client import Config
from botocore.exceptions import ClientError
from data_platform_helpers.advanced_statuses.models import StatusObject
from data_platform_helpers.advanced_statuses.protocol import ManagerStatusProtocol
from data_platform_helpers.advanced_statuses.types import Scope
from mypy_boto3_s3.service_resource import Bucket
from tenacity import RetryError, Retrying, stop_after_attempt, wait_fixed

from common.client import EtcdClient
from common.exceptions import EtcdBackupError
from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import (
    BACKUP_FILE_NAME,
    BACKUP_ID_FORMAT,
    DATABASE_DIR,
    INTERNAL_USER,
    SNAP_DATA_PATH,
    EtcdClusterState,
    RestoreStep,
)
from statuses import BackupStatuses, CharmStatuses

logger = logging.getLogger(__name__)


class BackupManager(ManagerStatusProtocol):
    """Manage everything related to backup and restore."""

    name = "backup"
    state: ClusterState

    def __init__(self, state: ClusterState, workload: WorkloadBase):
        self.state = state
        self.workload = workload
        self.admin_user = INTERNAL_USER
        self.admin_password = self.state.cluster.internal_user_credentials.get(INTERNAL_USER, "")

    def _get_bucket_resource(self, s3_parameters: dict[str, str]) -> Bucket:
        """Get the Bucket resource from the s3 connection.

        Returns:
            Bucket: the s3 bucket for uploading/downloading backups
        """
        s3_resource = boto3.resource(
            "s3",
            region_name=s3_parameters.get("region"),
            endpoint_url=s3_parameters["endpoint"],
            aws_access_key_id=s3_parameters["access-key"],
            aws_secret_access_key=s3_parameters["secret-key"],
            config=Config(
                # https://github.com/boto/boto3/issues/4400#issuecomment-2600742103
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
            verify=self.workload.paths.tls.backup_ca
            if s3_parameters.get("tls-ca-chain")
            else True,
        )

        return s3_resource.Bucket(s3_parameters["bucket"])

    def _get_container_client(self, azure_parameters: dict[str, str]) -> ContainerClient:
        """Get the Container client from the Azure connection.

        Returns:
            ContainerClient: the Azure container for uploading/downloading backups
        """
        return ContainerClient(
            account_url=f"https://{azure_parameters['storage-account']}.blob.core.windows.net",
            container_name=azure_parameters["container"],
            credential=azure_parameters["secret-key"],
        )

    def _get_etcd_client(self) -> EtcdClient:
        """Get a client connection to etcd."""
        return EtcdClient(
            username=self.admin_user,
            password=self.admin_password,
            client_url=self.state.unit_server.client_url,
        )

    def create_bucket(self, s3_parameters: dict[str, str]) -> None:
        """Create bucket if it does not exist yet."""
        region = s3_parameters.get("region")
        bucket = self._get_bucket_resource(s3_parameters)

        try:
            if region:
                bucket.create(CreateBucketConfiguration={"LocationConstraint": region})
            else:
                bucket.create()
            bucket.wait_until_exists()
        except ClientError as e:
            if (
                # AWS returns these if the bucket was already created
                "BucketAlreadyOwnedByYou" in e.args[0]
                or "BucketAlreadyExists" in e.args[0]
                # GCP returns this if the bucket was already created
                or "BucketNameUnavailable" in e.args[0]
            ):
                logger.info(f"Using existing bucket {s3_parameters['bucket']}")
                return
            else:
                logger.error(e)
                raise EtcdBackupError(e)

        logger.info(f"Bucket {s3_parameters['bucket']} is ready")

    def create_container(self, azure_parameters: dict[str, str]) -> None:
        """Create container if it does not exist yet."""
        container_client = self._get_container_client(azure_parameters)
        try:
            container_client.create_container()
            logger.info(f"Container {azure_parameters['container']} created")
        except ResourceExistsError:
            logger.info(f"Container {azure_parameters['container']} already exists")

    def create_backup(self) -> str:
        """Create a backup of etcd and upload it to object storage.

        Returns:
            str: the backup_id uploaded to object storage
        """
        backup_id = datetime.now().strftime(BACKUP_ID_FORMAT)
        # set flag in peer relation data to avoid concurring actions/events
        self.state.cluster.update({"backup_id": backup_id})

        if self.workload.alive():
            # online backup
            etcd_client = self._get_etcd_client()
            if not etcd_client.create_database_snapshot():
                self.state.cluster.update({"backup_id": ""})
                raise EtcdBackupError("Failed to create database backup.")
        else:
            # offline backup from `member/snap/db` file
            logger.info("Cluster is not running, creating offline backup")
            self.workload.copy_file(src_file=f"{DATABASE_DIR}/snap/db", dst_file=BACKUP_FILE_NAME)

        if s3_parameters := self.state.cluster.s3_credentials:
            # backup file will be uploaded to S3 storage
            upload_target = f"{s3_parameters['path']}/{backup_id}"
            bucket = self._get_bucket_resource(s3_parameters)

            try:
                for attempt in Retrying(
                    stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=True
                ):
                    with attempt:
                        bucket.upload_file(BACKUP_FILE_NAME, upload_target)
            except ClientError as e:
                self.state.cluster.update({"backup_id": ""})
                # if we can't upload, we still need to clean up the backup file to free the disk space
                self.workload.remove_file(BACKUP_FILE_NAME)
                logger.debug(f"Removed temporary snapshot file {BACKUP_FILE_NAME}")
                raise EtcdBackupError(e)
        else:
            # backup file will be uploaded to Azure storage
            azure_parameters = self.state.cluster.azure_credentials
            upload_target = f"{azure_parameters['path']}/{backup_id}"
            blob_client = self._get_container_client(azure_parameters).get_blob_client(
                upload_target
            )

            try:
                # we use `RetryError` because the blob client raises a multitude of exceptions
                for attempt in Retrying(
                    stop=stop_after_attempt(3), wait=wait_fixed(5), reraise=False
                ):
                    with attempt:
                        with open(BACKUP_FILE_NAME, "rb") as backup_file:
                            blob_client.upload_blob(backup_file)
            except RetryError as e:
                self.state.cluster.update({"backup_id": ""})
                # if we can't upload, we still need to clean up the backup file to free the disk space
                self.workload.remove_file(BACKUP_FILE_NAME)
                logger.debug(f"Removed temporary snapshot file {BACKUP_FILE_NAME}")
                raise EtcdBackupError(e)

        logger.info(f"Backup uploaded to {upload_target}")

        self.state.cluster.update({"backup_id": ""})
        self.workload.remove_file(BACKUP_FILE_NAME)
        logger.debug(f"Removed temporary snapshot file {BACKUP_FILE_NAME}")

        return backup_id

    def list_backups(self) -> list[str]:
        """Get the list of available backups in the configured object storage.

        Returns:
            list: the available backup_id's in the bucket
        """
        backup_list = []

        if s3_parameters := self.state.cluster.s3_credentials:
            # retrieve the list of backups from S3 storage
            path = s3_parameters["path"]
            bucket = self._get_bucket_resource(s3_parameters)

            try:
                bucket_objects = bucket.objects.filter(Prefix=path)
                for bucket_object in bucket_objects:
                    backup_list.append(bucket_object.key)
            except ClientError as e:
                raise EtcdBackupError(e)
        else:
            # retrieve the list of backups from Azure storage
            azure_parameters = self.state.cluster.azure_credentials
            path = azure_parameters["path"]

            container_objects = self._get_container_client(azure_parameters).list_blob_names(
                name_starts_with=path
            )
            for container_object in container_objects:
                backup_list.append(container_object)

        # current format: ['etcd-backups/2025-03-19T11:56:30Z','etcd-backups/2025-03-19T11:57:52Z']
        backup_list = [b.replace(f"{path}/", "") for b in backup_list]
        backup_list.sort(reverse=True)

        return backup_list

    def download_backup_file(self, backup_id: str) -> bool:
        """Initiate the restore process by downloading the provided backup-id from object storage.

        Returns:
            True if backup-file could be downloaded from object storage and restore process was
            initiated, False otherwise.
        """
        logger.info(f"Initiating restore process for backup-id {backup_id}")

        if s3_parameters := self.state.cluster.s3_credentials:
            # download the backup file from S3 storage
            download_source = f"{s3_parameters['path']}/{backup_id}"
            bucket = self._get_bucket_resource(s3_parameters)

            try:
                bucket.download_file(download_source, BACKUP_FILE_NAME)
            except ClientError as e:
                logger.error(e)
                return False
        else:
            # download the backup file from Azure storage
            azure_parameters = self.state.cluster.azure_credentials
            download_source = f"{azure_parameters['path']}/{backup_id}"
            blob_client = self._get_container_client(azure_parameters).get_blob_client(
                download_source
            )

            try:
                with open(BACKUP_FILE_NAME, mode="wb") as backup_file:
                    download_stream = blob_client.download_blob()
                    backup_file.write(download_stream.readall())
            except Exception as e:
                logger.error(e)
                return False

        logger.info(f"Backup {backup_id} downloaded to {BACKUP_FILE_NAME}")
        self.set_restore_step(RestoreStep.DOWNLOAD.value)
        return True

    def stop_database(self) -> None:
        """Shutdown and disable the etcd database before restoring."""
        logger.info("Stopping and disabling etcd workload.")
        # disable the service to avoid restart while the backup is restored
        self.workload.disable_database()

    def restore_backup(self) -> None:
        """Perform the actual restore-operation on the etcd database."""
        backup_id_to_restore = self.state.cluster.restore_id
        logger.info(f"Restoring database backup {backup_id_to_restore}")

        # existing data directory has to be purged, otherwise restore will fail
        try:
            self.workload.remove_directory(DATABASE_DIR)
            logger.info(f"Removed previous database files from {DATABASE_DIR} before restoring.")
        except FileNotFoundError:
            logger.info(f"No database file found in {DATABASE_DIR} - nothing to remove")

        etcd_client = self._get_etcd_client()

        if not etcd_client.restore_database_snapshot(
            snapshot_filename=BACKUP_FILE_NAME,
            data_directory=SNAP_DATA_PATH,
            cluster_config=self.state.unit_server.member_endpoint
            if self.state.cluster.restore_instruction == RestoreStep.VERIFY
            else self.state.cluster.cluster_members,
            peer_url=self.state.unit_server.peer_url,
            member_name=self.state.unit_server.member_name,
        ):
            raise EtcdBackupError("Failed to restore database backup.")

        logger.info("Restored backup successfully.")
        if self.state.cluster.restore_instruction == RestoreStep.VERIFY:
            return

        self.set_restore_step(RestoreStep.RESTORE.value)

    def start_database(self) -> None:
        """Enable and start the etcd database again after restoring."""
        logger.info("Enabling and starting etcd workload.")
        self.workload.enable_database()

    def clean_up_after_restore(self) -> None:
        """Remove backup files and state from unit."""
        logger.info("Removing backup file after restore completed.")

        self.workload.remove_file(BACKUP_FILE_NAME)
        self.set_restore_step("")

    @staticmethod
    def format_backup_list(backup_list: list[str]) -> str:
        """Format a list of backup_id's as a table and return the output.

        Returns:
            str: the backup_id's formatted as a table
        """
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

    @staticmethod
    def next_restore_step(current_step: RestoreStep) -> RestoreStep:
        """Define the order of steps for the restore workflow."""
        match current_step:
            case RestoreStep.NOT_STARTED:
                return RestoreStep.DOWNLOAD
            case RestoreStep.DOWNLOAD:
                return RestoreStep.STOP
            case RestoreStep.STOP:
                return RestoreStep.VERIFY
            case RestoreStep.VERIFY:
                return RestoreStep.RESTORE
            case RestoreStep.RESTORE:
                return RestoreStep.START
            case RestoreStep.START:
                return RestoreStep.COMPLETED

    def set_restore_step(self, restore_step: str) -> None:
        """Update the unit's current step in the databag."""
        self.state.unit_server.update({"restore_step": restore_step})

    def proceed_restore_workflow_if_possible(self) -> None:
        """Check workflow progress for all units and proceed to next step if possible."""
        current_step = self.state.cluster.restore_instruction

        if (
            current_step == RestoreStep.RESTORE
            and not self.state.cluster.restore_verification_failed
        ):
            # `cluster_state` must be reset before starting a restored cluster
            self.state.cluster.update({"cluster_state": EtcdClusterState.NEW.value})
        elif current_step == RestoreStep.COMPLETED:
            # clean up peer relation app data after restore workflow is done
            self.state.cluster.update(
                {
                    "restore_instruction": "",
                    "restore_id": "",
                    "cluster_state": EtcdClusterState.EXISTING.value,
                }
            )
            return

        if self.state.can_restore_workflow_proceed:
            next_step = self.next_restore_step(current_step)
            logger.info(f"Next restore step: {next_step.value}")
            self.state.cluster.update({"restore_instruction": next_step.value})

    def get_statuses(self, scope: Scope, recompute: bool = False) -> list[StatusObject]:
        """Compute the Backup manager's statuses."""
        if recompute:
            self.state.statuses.clear(scope=scope, component=self.name)

        status_list: list[StatusObject] = self.state.statuses.get(
            scope=scope, component=self.name
        ).root

        if self.state.cluster.is_backup_in_progress:
            status_list.append(BackupStatuses.BACKUP_IN_PROGRESS.value)

        if self.state.cluster.is_restore_in_progress:
            status_list.append(BackupStatuses.RESTORE_IN_PROGRESS.value)

        if self.state.cluster.restore_verification_failed:
            status_list.append(BackupStatuses.RESTORE_VERIFICATION_FAILED.value)

        if self.state.cluster.s3_credentials and self.state.cluster.azure_credentials:
            status_list.append(BackupStatuses.OBJECT_STORAGE_CONFLICT.value)

        if self.state.s3_relation and not self.state.cluster.s3_credentials:
            status_list.append(BackupStatuses.BACKUP_S3_PARAMETERS_MISSING.value)

        if self.state.azure_relation and not self.state.cluster.azure_credentials:
            status_list.append(BackupStatuses.BACKUP_AZURE_PARAMETERS_MISSING.value)

        return status_list or [CharmStatuses.ACTIVE_IDLE.value]
