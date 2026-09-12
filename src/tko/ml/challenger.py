"""Immutable Challenger candidate registry."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChallengerRecord:
    model_id: str
    version: str
    artifact_hash: str
    artifact_dir: str
    dataset_version: str
    feature_version: str
    training_window: str
    validation_window: str
    metrics: dict[str, Any]
    dsr: float
    pbo_note: str
    max_drawdown: float
    profit_factor: float
    trade_frequency: float
    gate_results: dict[str, Any]
    status: str  # REGISTERED|SHADOW_ELIGIBLE|SHADOW_ACTIVE|REJECTED|PROMOTED
    created_ts: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChallengerRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, rec: ChallengerRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict()) + "\n")

    def register(self, **kwargs: Any) -> ChallengerRecord:
        mid = kwargs.get("model_id") or uuid.uuid4().hex[:16]
        rec = ChallengerRecord(
            model_id=str(mid),
            version=str(kwargs.get("version", "1")),
            artifact_hash=str(kwargs["artifact_hash"]),
            artifact_dir=str(kwargs.get("artifact_dir", "")),
            dataset_version=str(kwargs.get("dataset_version", "")),
            feature_version=str(kwargs.get("feature_version", "")),
            training_window=str(kwargs.get("training_window", "")),
            validation_window=str(kwargs.get("validation_window", "")),
            metrics=dict(kwargs.get("metrics") or {}),
            dsr=float(kwargs.get("dsr", 0.0)),
            pbo_note=str(kwargs.get("pbo_note", "")),
            max_drawdown=float(kwargs.get("max_drawdown", 0.0)),
            profit_factor=float(kwargs.get("profit_factor", 0.0)),
            trade_frequency=float(kwargs.get("trade_frequency", 0.0)),
            gate_results=dict(kwargs.get("gate_results") or {}),
            status=str(kwargs.get("status", "REGISTERED")),
            created_ts=float(kwargs.get("created_ts", time.time())),
        )
        self._append(rec)
        return rec

    def update_status(self, model_id: str, status: str, *, gate_results: dict | None = None) -> ChallengerRecord | None:
        cur = self.latest(model_id)
        if cur is None:
            return None
        # immutability of artifact fields — only status/gates may change via new append
        rec = ChallengerRecord(
            model_id=cur.model_id,
            version=cur.version,
            artifact_hash=cur.artifact_hash,
            artifact_dir=cur.artifact_dir,
            dataset_version=cur.dataset_version,
            feature_version=cur.feature_version,
            training_window=cur.training_window,
            validation_window=cur.validation_window,
            metrics=dict(cur.metrics),
            dsr=cur.dsr,
            pbo_note=cur.pbo_note,
            max_drawdown=cur.max_drawdown,
            profit_factor=cur.profit_factor,
            trade_frequency=cur.trade_frequency,
            gate_results=dict(gate_results if gate_results is not None else cur.gate_results),
            status=status,
            created_ts=cur.created_ts,
        )
        self._append(rec)
        return rec

    def latest(self, model_id: str) -> ChallengerRecord | None:
        last = None
        if not self.path.exists():
            return None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("model_id") == model_id:
                last = ChallengerRecord(**d)
        return last

    def all_latest(self) -> dict[str, ChallengerRecord]:
        by: dict[str, ChallengerRecord] = {}
        if not self.path.exists():
            return by
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = ChallengerRecord(**json.loads(line))
                by[r.model_id] = r
        return by
