"""Sanitize arbitrary JSON-like payloads and flatten them for LLM extraction."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any


MAX_VALUE_CHARS = 5000
MAX_OUTPUT_BYTES = 50 * 1024
TRUNCATED_SUFFIX = "[truncated]"
PRIORITY_KEYS = {"detail", "content", "text", "description", "summary"}

_TECHNICAL_ID_KEY_RE = re.compile(r"^(id|_id|uuid|pk)$", re.IGNORECASE)
_INTEGER_RE = re.compile(r"^\d+$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)
_BASE64_CHARS_RE = re.compile(r"^[A-Za-z0-9+/=\s]+$")


def flatten_json(data: Any) -> str:
    """Return sanitized, flattened text capped at 50KB UTF-8 bytes."""
    lines = _flatten(data)
    return _truncate_utf8("\n".join(line for line in lines if line), MAX_OUTPUT_BYTES)


def _flatten(value: Any, key: str | None = None) -> list[str]:
    if _is_empty(value) or _is_base64_blob(value):
        return []

    if isinstance(value, dict):
        return _flatten_dict(value, key)
    if isinstance(value, list):
        return _flatten_list(value, key)

    scalar = _sanitize_scalar(value, key)
    if scalar == "":
        return []
    return [f"{key}: {scalar}" if key else scalar]


def _flatten_dict(data: dict[Any, Any], parent_key: str | None = None) -> list[str]:
    lines: list[str] = []
    items = [(str(key), value) for key, value in data.items()]
    items.sort(key=lambda item: (0 if item[0] in PRIORITY_KEYS else 1))

    for raw_key, value in items:
        if _is_empty(value) or _is_base64_blob(value):
            continue
        key = f"{parent_key}.{raw_key}" if parent_key else raw_key
        if isinstance(value, dict):
            nested = _flatten_dict(value, key)
            lines.extend(nested)
        elif isinstance(value, list):
            nested = _flatten_list(value, key)
            if nested:
                lines.append(f"{key}:")
                lines.extend(nested)
        else:
            scalar = _sanitize_scalar(value, raw_key)
            if scalar != "":
                lines.append(f"{key}: {scalar}")
    return lines


def _flatten_list(data: list[Any], parent_key: str | None = None) -> list[str]:
    lines: list[str] = []
    visible_index = 1
    for item in data:
        if _is_empty(item) or _is_base64_blob(item):
            continue
        prefix = f"{visible_index}."
        if isinstance(item, dict):
            nested = _flatten_dict(item)
            if nested:
                lines.append(prefix)
                lines.extend(f"  {line}" for line in nested)
                visible_index += 1
        elif isinstance(item, list):
            nested = _flatten_list(item)
            if nested:
                lines.append(prefix)
                lines.extend(f"  {line}" for line in nested)
                visible_index += 1
        else:
            scalar = _sanitize_scalar(item, parent_key)
            if scalar != "":
                lines.append(f"{prefix} {scalar}")
                visible_index += 1
    return lines


def _sanitize_scalar(value: Any, key: str | None = None) -> str:
    text = str(value)
    if _is_empty(text) or _is_base64_blob(text):
        return ""
    if key and _TECHNICAL_ID_KEY_RE.match(key) and _is_technical_id_value(text):
        return "<id>"
    if len(text) > MAX_VALUE_CHARS:
        return f"{text[:MAX_VALUE_CHARS]} {TRUNCATED_SUFFIX}"
    return text


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _is_technical_id_value(value: str) -> bool:
    stripped = value.strip()
    return bool(_INTEGER_RE.match(stripped) or _UUID_RE.match(stripped))


def _is_base64_blob(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if stripped.startswith("data:") and ";base64," in stripped[:128]:
        return True
    if len(stripped) <= 200 or not _BASE64_CHARS_RE.match(stripped):
        return False
    compact = re.sub(r"\s+", "", stripped)
    try:
        base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        return False
    return True


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
