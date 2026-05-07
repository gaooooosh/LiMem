"""Service-side authentication package: SQLite-backed users / api_keys / databases."""

from .repository import (
    ApiKey,
    AuthRepository,
    Database,
    User,
    DatabaseAlreadyExistsError,
    KeyNotFoundError,
    UserAlreadyExistsError,
    UserNotFoundError,
)
from .hashing import generate_token, hash_token

__all__ = [
    "ApiKey",
    "AuthRepository",
    "Database",
    "User",
    "DatabaseAlreadyExistsError",
    "KeyNotFoundError",
    "UserAlreadyExistsError",
    "UserNotFoundError",
    "generate_token",
    "hash_token",
]
