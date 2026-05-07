"""Token generation and hashing helpers for the auth layer."""

from __future__ import annotations

import hashlib
import secrets


def generate_token(byte_length: int = 32) -> str:
    """生成 URL 安全的随机 token 明文，仅在签发响应里返回一次。"""
    return secrets.token_urlsafe(byte_length)


def hash_token(token: str) -> str:
    """对 token 取 sha256 hex 摘要，库内只存这个值。"""
    if not token:
        raise ValueError("token must be a non-empty string")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
