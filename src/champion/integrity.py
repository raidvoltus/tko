"""Registry integrity helpers — content checksum, not cryptographic signing service."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict


def canonical_json(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_digest(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def write_integrity_sidecar(registry_path: Path, payload: Dict[str, Any]) -> Path:
    """Write registry JSON + .sha256 sidecar for tamper detection (not PKI-signed)."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2)
    registry_path.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    side = registry_path.with_suffix(registry_path.suffix + ".sha256")
    side.write_text(digest + "  " + registry_path.name + "\n", encoding="utf-8")
    return side


def verify_integrity_sidecar(registry_path: Path) -> bool:
    side = registry_path.with_suffix(registry_path.suffix + ".sha256")
    if not registry_path.exists() or not side.exists():
        return False
    body = registry_path.read_text(encoding="utf-8")
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    expected = side.read_text(encoding="utf-8").split()[0].strip()
    return actual == expected
