"""Authenticate user and administrator API keys without HTTP fallbacks."""

from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest
from typing import TYPE_CHECKING

from .models import KeyRecord

if TYPE_CHECKING:
    from .repository import ServiceRepository

API_KEY_HEADER = "X-API-Key"


class ApiKeyAuthenticationError(Exception):
    """The presented user API key is unknown or revoked."""


class AdminAuthenticationError(Exception):
    """The presented API key does not match the configured administrator key."""


def hash_api_key(api_key: str) -> str:
    """Return the SHA-256 digest stored for an API key."""

    return sha256(api_key.encode("utf-8")).hexdigest()


class ApiKeyAuthenticator:
    """Resolve one user API key to its active key identity."""

    def __init__(self, repository: ServiceRepository) -> None:
        self._repository = repository

    def authenticate(self, api_key: str) -> KeyRecord:
        record = self._repository.find_key_by_hash(hash_api_key(api_key))
        if record is None or record.status != "active":
            raise ApiKeyAuthenticationError
        return record


class AdminAuthenticator:
    """Authenticate the separately configured administrator API key."""

    def __init__(self, admin_api_key: str) -> None:
        if not admin_api_key:
            raise ValueError("admin_api_key must not be empty")
        self._admin_api_key = admin_api_key

    def authenticate(self, api_key: str) -> None:
        if not compare_digest(api_key, self._admin_api_key):
            raise AdminAuthenticationError
