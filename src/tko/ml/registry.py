"""Append-only model registry with explicit state machine."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ModelState(str, Enum):
    TRAINED = "TRAINED"
    VALIDATED = "VALIDATED"
    CALIBRATED = "CALIBRATED"
    ELIGIBLE = "ELIGIBLE"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"
    CORRUPTED = "CORRUPTED"


# Allowed transitions (fail-closed otherwise)
_TRANSITIONS: dict[ModelState, set[ModelState]] = {
    ModelState.TRAINED: {ModelState.VALIDATED, ModelState.REJECTED, ModelState.CORRUPTED},
    ModelState.VALIDATED: {ModelState.CALIBRATED, ModelState.REJECTED, ModelState.CORRUPTED},
    ModelState.CALIBRATED: {ModelState.ELIGIBLE, ModelState.REJECTED, ModelState.CORRUPTED},
    ModelState.ELIGIBLE: {ModelState.SHADOW, ModelState.REJECTED, ModelState.CORRUPTED},
    ModelState.SHADOW: {ModelState.ACTIVE, ModelState.REJECTED, ModelState.RETIRED, ModelState.CORRUPTED},
    ModelState.ACTIVE: {ModelState.RETIRED, ModelState.CORRUPTED, ModelState.REJECTED},
    ModelState.REJECTED: {ModelState.RETIRED},
    ModelState.RETIRED: set(),
    ModelState.CORRUPTED: set(),
}


@dataclass
class ModelRecord:
    model_id: str
    state: str
    created_at: float
    updated_at: float
    artifact_dir: str = ""
    run_id: str = ""
    feature_version: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    decision: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelRegistry:
    """JSONL append-only registry. Active model pointer is separate file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.active_path = path.with_suffix(".active.json")

    def _append(self, record: ModelRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_dict()) + "\n")

    def register_trained(
        self,
        *,
        artifact_dir: str,
        run_id: str,
        feature_version: str,
        metrics: dict[str, Any] | None = None,
    ) -> ModelRecord:
        now = time.time()
        rec = ModelRecord(
            model_id=uuid.uuid4().hex[:16],
            state=ModelState.TRAINED.value,
            created_at=now,
            updated_at=now,
            artifact_dir=artifact_dir,
            run_id=run_id,
            feature_version=feature_version,
            metrics=dict(metrics or {}),
        )
        self._append(rec)
        return rec

    def transition(self, model_id: str, new_state: ModelState, *, notes: str = "", metrics: dict | None = None) -> ModelRecord | None:
        current = self.latest(model_id)
        if current is None:
            return None
        cur = ModelState(current.state)
        if new_state not in _TRANSITIONS.get(cur, set()):
            raise ValueError(f"illegal transition {cur.value} → {new_state.value}")
        now = time.time()
        rec = ModelRecord(
            model_id=model_id,
            state=new_state.value,
            created_at=current.created_at,
            updated_at=now,
            artifact_dir=current.artifact_dir,
            run_id=current.run_id,
            feature_version=current.feature_version,
            metrics=dict(metrics or current.metrics),
            decision=new_state.value,
            notes=notes,
        )
        self._append(rec)
        return rec

    def latest(self, model_id: str) -> ModelRecord | None:
        last = None
        if not self.path.exists():
            return None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("model_id") == model_id:
                last = ModelRecord(**d)
        return last

    def all_records(self) -> list[ModelRecord]:
        out: list[ModelRecord] = []
        if not self.path.exists():
            return out
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(ModelRecord(**json.loads(line)))
        return out

    def set_active(self, model_id: str) -> None:
        rec = self.latest(model_id)
        if rec is None or rec.state != ModelState.ACTIVE.value:
            raise ValueError("only ACTIVE models may be set as active pointer")
        payload = {"model_id": model_id, "artifact_dir": rec.artifact_dir, "updated_at": time.time()}
        tmp = self.active_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.active_path)

    def get_active(self) -> dict[str, Any] | None:
        if not self.active_path.exists():
            return None
        try:
            return json.loads(self.active_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def clear_active(self) -> None:
        if self.active_path.exists():
            self.active_path.unlink(missing_ok=True)
