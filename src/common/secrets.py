# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utility functions related to secrets."""

import logging

from ops.model import ModelError, SecretNotFoundError

logger = logging.getLogger(__name__)


def get_secret_from_id(model, secret_id: str) -> dict[str, str] | None:
    """Resolve the given id of a Juju secret and return the content as a dict."""
    try:
        secret_content = model.get_secret(id=secret_id).get_content(refresh=True)
    except SecretNotFoundError:
        raise SecretNotFoundError(f"The secret '{secret_id}' does not exist.")
    except ModelError:
        raise

    return secret_content


def get_secret_from_label(model, label: str) -> dict[str, str] | None:
    """Resolve the given label of a Juju secret and return the content as a dict."""
    try:
        secret_content = model.get_secret(label=label).get_content(refresh=True)
    except SecretNotFoundError:
        raise SecretNotFoundError(f"The secret '{label}' does not exist.")
    except ModelError:
        raise

    return secret_content
