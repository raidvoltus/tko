"""Central secret redaction before logging / exceptions."""
from __future__ import annotations

import re
from typing import Any, Optional, Set

_PATTERNS = [
    re.compile(r"(?i)(api[_-]?secret|api[_-]?key|secret[_-]?key|bot[_-]?token|authorization|bearer)\s*[:=]\s*['\"]?([^\s'\"]+)", re.I),
    re.compile(r"(?i)bot\d{6,}:[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)(x-mbx-apiKey|signature)=([A-Za-z0-9+/=_-]{8,})"),
]


class SecretRedactor:
    def __init__(self) -> None:
        self._secrets: Set[str] = set()

    def register(self, *values: Optional[str]) -> None:
        for v in values:
            if v and len(str(v)) >= 6:
                self._secrets.add(str(v))

    def redact(self, text: str) -> str:
        if not text:
            return text
        out = text
        for s in sorted(self._secrets, key=len, reverse=True):
            if s in out:
                out = out.replace(s, "***REDACTED***")
        for pat in _PATTERNS:
            out = pat.sub(lambda m: m.group(0)[: m.group(0).find(m.group(m.lastindex))] + "***REDACTED***" if m.lastindex else "***REDACTED***", out)
            # simpler:
        for pat in _PATTERNS:
            out = pat.sub("***REDACTED***", out)
        return out

    def redact_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: ("***REDACTED***" if str(k).lower() in ("api_key", "api_secret", "secret", "bot_token", "token", "password") else self.redact_obj(v)) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact_obj(x) for x in obj]
        return obj


_GLOBAL = SecretRedactor()


def get_redactor() -> SecretRedactor:
    return _GLOBAL


def redact(text: str) -> str:
    return _GLOBAL.redact(text)
