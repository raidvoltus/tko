"""Secret redaction for logs, audit, heartbeat, and exception surfaces."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|secret|password|token|authorization|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|bearer|credential)",
    re.IGNORECASE,
)

_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|password|token|authorization|bearer)"
    r"\s*[:=]\s*['\"]?[^\s'\",;]{8,}"
)

_REDACTED = "***REDACTED***"


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key or "")))


def redact_text(text: str | None) -> str:
    if not text:
        return ""
    return _SECRET_VALUE_RE.sub(r"\1=***REDACTED***", str(text))


def redact_mapping(data: dict[str, Any] | None, *, depth: int = 0) -> dict[str, Any]:
    """Return a shallow-safe copy with sensitive keys/values redacted."""
    if not data or depth > 4:
        return {}
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = str(k)
        if is_sensitive_key(key):
            out[key] = _REDACTED
            continue
        if isinstance(v, dict):
            out[key] = redact_mapping(v, depth=depth + 1)
        elif isinstance(v, str):
            out[key] = redact_text(v)
        else:
            out[key] = v
    return out
