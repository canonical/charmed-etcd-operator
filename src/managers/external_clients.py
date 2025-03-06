#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling external clients."""

import json
import logging
from pathlib import Path

from charms.tls_certificates_interface.v4.tls_certificates import Certificate

from core.cluster import ClusterState
from core.workload import WorkloadBase
from literals import SUBSTRATES

logger = logging.getLogger(__name__)

WORKING_DIR = Path(__file__).absolute().parent


class ExternalClientsManager:
    """Handle the external clients related logic."""

    def __init__(
        self,
        state: ClusterState,
        workload: WorkloadBase,
        substrate: SUBSTRATES,
    ):
        self.state = state
        self.workload = workload
        self.substrate = substrate

    def remove_managed_user(self, relation_id: int):
        """Remove the user.

        Args:
            relation_id (int): The relation id.
        """
        managed_users = self.state.cluster.managed_users
        del managed_users[relation_id]
        self.state.cluster.update(
            {
                "managed_users": json.dumps(managed_users),
            }
        )

    def add_managed_user(self, relation_id: int, common_name: str):
        """Add the user.

        Args:
            relation_id (int): The relation id.
            common_name (str): The common name.
        """
        managed_users = self.state.cluster.managed_users
        managed_users[relation_id] = common_name

        self.state.cluster.update(
            {
                "managed_users": json.dumps(managed_users),
            }
        )

    def get_relation_managed_user(self, relation_id: int) -> str | None:
        """Get the relation's managed user.

        Args:
            relation_id (int): The relation id.

        Returns:
            (str): The managed user.
        """
        return self.state.cluster.managed_users.get(relation_id)

    def get_common_name_from_chain(self, mtls_chain: str) -> str:
        """Get the common name from the mtls chain.

        Args:
            mtls_chain (str): The mtls chain.

        Returns:
            (str): The common name.
        """
        # split the certificates by the end of the certificate marker and keep the marker in the cert
        raw_cas = mtls_chain.split("-----END CERTIFICATE-----")
        # add the marker back to the certificate
        cert = raw_cas[0].strip() + "\n-----END CERTIFICATE-----"
        return Certificate.from_string(cert).common_name
