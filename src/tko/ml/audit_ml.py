"""Immutable ML lifecycle audit trail — no secrets."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class AuditEvent:
    ts: float
    event: str
    model_id: str = ""
    version: str = ""
    artifact_hash: str = ""
    reason: str = ""
    gate_results: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("gate_results") is None:
            d.pop("gate_results", None)
        return d


class MlAuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(
        self,
        event: str,
        *,
        model_id: str = "",
        version: str = "",
        artifact_hash: str = "",
        reason: str = "",
        gate_results: dict[str, Any] | None = None,
    ) -> None:
        # strip potential secret-like keys from gate_results
        safe_gates = None
        if gate_results is not None:
            safe_gates = {
                k: v
                for k, v in gate_results.items()
                if not any(s in k.lower() for s in ("key", "secret", "token", "password"))
            }
        rec = AuditEvent(
            ts=time.time(),
            event=event,
            model_id=model_id,
            version=version,
            artifact_hash=artifact_hash,
            reason=reason,
            gate_results=safe_gates,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict()) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
