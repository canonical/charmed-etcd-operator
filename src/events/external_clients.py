#!/usr/bin/env python3
# Copyright 2025 Canonical Limited
# See LICENSE file for licensing details.

"""External clients related event handlers."""

import logging
from typing import TYPE_CHECKING

from charms.certificate_transfer_interface.v1.certificate_transfer import (
    CertificatesAvailableEvent,
    CertificatesRemovedEvent,
    CertificateTransferRequires,
)
from dpcharmlibs.interfaces import (
    BulkResourcesRequestedEvent,
    MtlsCertUpdatedEvent,
    RequirerCommonModel,
    ResourceProviderEventHandler,
    ResourceProviderModel,
)
from ops import Object, Relation, RelationBrokenEvent

from common.certificates import is_leaf_certificate_valid
from common.exceptions import EtcdUserManagementError
from literals import (
    CERTIFICATE_TRANSFER_RELATION,
    EXTERNAL_CLIENTS_RELATION,
    TLSCARotationState,
    TLSState,
    TLSType,
)
from statuses import ExternalClientsStatuses

if TYPE_CHECKING:
    from charm import EtcdOperatorCharm

logger = logging.getLogger(__name__)


class ExternalClientsEvents(Object):
    """Handle all base and etcd related events."""

    def __init__(self, charm: "EtcdOperatorCharm"):
        super().__init__(charm, key="etcd_events")
        self.charm = charm

        self.etcd_provides = ResourceProviderEventHandler(
            self.charm,
            EXTERNAL_CLIENTS_RELATION,
            RequirerCommonModel,
            mtls_enabled=True,
            bulk_event=True,
        )

        self.certificate_transfer = CertificateTransferRequires(
            self.charm, CERTIFICATE_TRANSFER_RELATION
        )
        self.framework.observe(
            self.certificate_transfer.on.certificate_set_updated, self._on_certificates_available
        )
        self.framework.observe(
            self.certificate_transfer.on.certificates_removed, self._on_certificates_removed
        )

        self.framework.observe(self.etcd_provides.on.mtls_cert_updated, self._on_mtls_cert_updated)
        self.framework.observe(
            self.etcd_provides.on.bulk_resources_requested, self._on_bulk_resources_requested
        )
        self.framework.observe(
            self.charm.on[EXTERNAL_CLIENTS_RELATION].relation_broken, self._on_relation_broken
        )

    def _on_bulk_resources_requested(  # noqa: C901
        self, event: BulkResourcesRequestedEvent[RequirerCommonModel]
    ) -> None:
        """Handle bulk resources requested event."""
        preventing_reason = self._exists_preventing_reason()
        if preventing_reason or not self.charm.unit.is_leader():
            event.defer()
            return

        invalid_requests = []

        responses = []
        for request in event.requests:
            if not request.mtls_cert:
                logger.error("mTLS certificate not provided")
                invalid_requests.append(request)
                continue

            common_name = self.charm.external_clients_manager.get_common_name_from_chain(
                request.mtls_cert
            )

            # validate leaf certificate
            if not is_leaf_certificate_valid(request.mtls_cert):
                logger.error("Invalid end-entity certificate for user %s", common_name)
                invalid_requests.append(request)
                continue

            relation_managed_user = self.charm.external_clients_manager.get_relation_managed_user(
                event.relation.id, request.request_id
            )

            if relation_managed_user and relation_managed_user == common_name:
                logger.warning("User already created for this request in the relation")
                continue

            if self.charm.cluster_manager.get_user(common_name) is not None:
                logger.error("User already exists in database for another request")
                invalid_requests.append(request)
                continue

            if relation_managed_user:
                self._remove_user(relation_managed_user, event.relation.id, request.request_id)

            if not self._add_user(common_name, request, event.relation.id):
                continue

            responses.append(
                self._get_or_create_resource_response(
                    relation=event.relation,
                    common_name=common_name,
                    request_id=request.request_id,
                    resource=request.resource,
                    salt=request.salt,
                )
            )
        if responses:
            self.etcd_provides.set_responses(event.relation.id, responses)
            self._update_client_truststore()

        if invalid_requests:
            logger.error("Invalid requests found: %s", invalid_requests)

    def _on_mtls_cert_updated(self, event: MtlsCertUpdatedEvent[RequirerCommonModel]) -> None:  # noqa: C901
        """Handle the ca chain updated event."""
        if not event.request.mtls_cert:
            logger.error("CA chain not provided")
            return

        if self._exists_preventing_reason():
            event.defer()
            return

        # Get common name from mtls_cert
        old_common_name = None
        if self.charm.external_clients_manager.get_relation_managed_user(
            event.relation.id, event.request.request_id
        ):
            old_common_name = (
                self.charm.external_clients_manager.get_common_name_from_chain(event.old_mtls_cert)
                if event.old_mtls_cert
                else None
            )
        common_name = self.charm.external_clients_manager.get_common_name_from_chain(
            event.request.mtls_cert
        )

        # validate leaf certificate
        if not is_leaf_certificate_valid(event.request.mtls_cert):
            logger.error("Invalid end-entity certificate")
            # clean the old user if exists
            if old_common_name:
                self._on_relation_broken(event)  # type: ignore
                return
            return

        # if leader then create/update user
        if self.charm.unit.is_leader():
            if old_common_name != common_name:
                logger.debug(f"Common name changed from {old_common_name} to {common_name}")

                # The old username is deleted even if creating the new user fails
                if old_common_name:
                    self._remove_user(old_common_name, event.relation.id, event.request.request_id)

                relation_managed_user = (
                    self.charm.external_clients_manager.get_relation_managed_user(
                        event.relation.id, event.request.request_id
                    )
                )
                if self.charm.cluster_manager.get_user(common_name) is not None:
                    if common_name == relation_managed_user:
                        logger.debug("User is already being added for this relation")
                    else:
                        logger.error("User already exists")
                    return

                if relation_managed_user is None:
                    if not self._add_user(common_name, event.request, event.relation.id):
                        return
                    self.etcd_provides.set_response(
                        event.relation.id,
                        self._get_or_create_resource_response(
                            relation=event.relation,
                            common_name=common_name,
                            request_id=event.request.request_id,
                            resource=event.request.resource,
                            salt=event.request.salt,
                        ),
                    )

        relation_managed_user = self.charm.external_clients_manager.get_relation_managed_user(
            event.relation.id, event.request.request_id
        )

        if relation_managed_user != common_name:
            logger.error("New user not created yet")
            event.defer()
            return

        self._update_client_truststore()

        self.charm.state.statuses.delete(
            ExternalClientsStatuses.EC_USER_MANAGEMENT_ERROR.value,
            scope="app",
            component=self.charm.external_clients_manager.name,
        )

    def _remove_user(
        self, old_common_name: str, relation_id: int, request_id: str | None = None
    ) -> None:
        """Remove an existing managed user from the etcd cluster.

        Args:
            old_common_name (str): The common name of the user to be removed.
            relation_id (int): The relation id.
            request_id (str): The request id.
        """
        logger.warning("Removing relation's old user")
        try:
            self.charm.cluster_manager.remove_managed_user(old_common_name)
        except EtcdUserManagementError as e:
            logger.error(f"Failed to remove old user from etcd: {e}")
        self.charm.external_clients_manager.remove_managed_user(
            relation_id=relation_id, request_id=request_id
        )

    def _get_or_create_resource_response(
        self,
        relation: Relation,
        common_name: str,
        request_id: str | None,
        resource: str,
        salt: str,
    ) -> ResourceProviderModel:
        """Get or create the resource response for the relation.

        Args:
            relation (Relation): The relation of the event.
            common_name (str): The common name of the user.
            request_id (str): The request id.
            resource (str): The resource requested.
            salt (str): The salt used for password hashing.

        Returns:
            The ResourceProviderModel response.
        """
        response = next(
            (
                res
                for res in self.etcd_provides.responses(relation, ResourceProviderModel)
                if res.request_id == request_id
            ),
            None,
        ) or ResourceProviderModel(
            username=common_name,
            request_id=request_id,
            resource=resource,
            salt=salt,
        )

        response.username = common_name
        response.endpoints = self.charm.external_clients_manager.get_endpoints()
        response.uris = self.charm.external_clients_manager.get_uris()
        response.tls_ca = self.charm.state.tls_client_certificate.ca.raw
        response.version = self.charm.cluster_manager.get_version()

        return response

    def _on_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Handle the relation broken event."""
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
            or self.charm.refresh_in_progress
        ):
            logger.warning(
                "Cannot remove client relation while cluster is in vulnerable state because of restore, refresh or cluster-rebuild"
            )
            event.defer()
            return

        if self.charm.unit.is_leader():
            for key in list(self.charm.state.cluster.model.managed_users.keys()):
                if not key.startswith(f"{event.relation.id}"):
                    continue
                user = self.charm.state.cluster.model.managed_users[key]

                request_id = key.split("-", 1)[1] if "-" in key else None

                try:
                    self.charm.cluster_manager.remove_managed_user(user)
                except EtcdUserManagementError as e:
                    logger.error(f"Failed to remove user from etcd: {e}")
                self.charm.external_clients_manager.remove_managed_user(
                    event.relation.id, request_id
                )

        self._update_client_truststore()

    def _on_certificates_available(self, event: CertificatesAvailableEvent) -> None:
        """Handle the certificates available event."""
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
            or self.charm.refresh_in_progress
        ):
            logger.warning(
                "Cannot update certificates while cluster is in vulnerable state because of restore, refresh or cluster-rebuild"
            )
            event.defer()
            return

        logger.debug("Certificates available event")
        if (
            self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            logger.debug("CA rotation is in progress")
            event.defer()
            return

        if self.certificate_transfer.get_all_certificates():
            self._update_client_truststore()

    def _on_certificates_removed(self, event: CertificatesRemovedEvent) -> None:
        """Handle the certificates removed event."""
        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
            or self.charm.refresh_in_progress
        ):
            logger.warning(
                "Cannot update certificates while cluster is in vulnerable state because of restore, refresh or cluster-rebuild"
            )
            event.defer()
            return

        if (
            self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            logger.debug("CA rotation is in progress")
            event.defer()
            return
        self._update_client_truststore()

    def _update_client_truststore(self) -> None:
        """Update the client truststore and Initiate a rolling restart of the cluster."""
        all_cas = self.charm.tls_manager.collect_client_cas()
        if all_cas != self.charm.tls_manager.load_trusted_ca(TLSType.CLIENT):
            logger.debug("CAs have changed, updating client truststore")
            self.charm.tls_manager.update_cas(all_cas, TLSType.CLIENT)
            self.charm.rolling_restart("_restart_ca_rotation")

    def _exists_preventing_reason(self) -> bool:
        """Check if there is any reason preventing handling external clients relations.

        Returns:
            A tuple where the first element indicates if there is a preventing reason
        """
        if not self.charm.state.cluster.model or not self.charm.state.unit_server.model:
            logger.error("peer data not available")
            return True

        if (
            self.charm.state.cluster.is_restore_in_progress
            or self.charm.state.cluster.rebuild_cluster_in_progress
            or self.charm.refresh_in_progress
        ):
            logger.warning(
                "Cannot update certificates while cluster is in vulnerable state because of restore, refresh or cluster-rebuild"
            )
            return True

        if self.charm.state.unit_server.tls_client_state in [TLSState.NO_TLS, TLSState.TO_NO_TLS]:
            logger.error("TLS is not enabled")
            return True

        if self.charm.state.unit_server.tls_client_state == TLSState.TO_TLS:
            logger.error("TLS is not ready")
            return True

        if (
            self.charm.state.unit_server.tls_client_ca_rotation_state
            != TLSCARotationState.NO_ROTATION
        ):
            logger.debug("CA rotation is in progress")
            return True

        if not self.charm.state.cluster.auth_enabled:
            logger.error("Cluster authentication is not enabled")
            return True

        return False

    def _add_user(
        self,
        common_name: str,
        request: RequirerCommonModel,
        relation_id: int,
    ) -> bool:
        """Add a managed user to the etcd cluster.

        Args:
            common_name (str): The common name of the user.
            request (RequirerCommonModel): The resource request.
            relation_id (int): The relation id.

        Returns:
            bool: True if the user was added successfully, False otherwise.
        """
        logger.info(f"Creating new user: {common_name}")
        try:
            self.charm.cluster_manager.add_managed_user(common_name, request.resource)
        except EtcdUserManagementError as e:
            self.charm.status.set_running_status(
                ExternalClientsStatuses.EC_USER_MANAGEMENT_ERROR.value,
                scope="app",
                component_name=self.charm.external_clients_manager.name,
                statuses_state=self.charm.state.statuses,
            )
            logger.error(e)
            return False

        self.charm.external_clients_manager.add_managed_user(
            relation_id, request.request_id, common_name
        )
        return True
