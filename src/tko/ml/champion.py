"""Immutable Champion registry state."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChampionRecord:
    model_id: str
    version: str
    artifact_hash: str
    artifact_dir: str
    dataset_version: str
    feature_version: str
    training_ts: float
    evaluation_window: str
    gate_results: dict[str, Any]
    promotion_ts: float
    status: str  # CHAMPION_ACTIVE | CHAMPION_RETIRED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChampionRegistry:
    """Single active champion pointer + append-only history. Immutable records."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.active_path = path.with_suffix(".active.json")

    def _append(self, rec: ChampionRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec.to_dict()) + "\n")

    def set_champion(self, rec: ChampionRecord) -> None:
        if rec.status != "CHAMPION_ACTIVE":
            raise ValueError("only CHAMPION_ACTIVE may be set")
        # retire previous active if any
        prev = self.get_active()
        if prev and prev.model_id != rec.model_id:
            retired = ChampionRecord(
                model_id=prev.model_id,
                version=prev.version,
                artifact_hash=prev.artifact_hash,
                artifact_dir=prev.artifact_dir,
                dataset_version=prev.dataset_version,
                feature_version=prev.feature_version,
                training_ts=prev.training_ts,
                evaluation_window=prev.evaluation_window,
                gate_results=dict(prev.gate_results),
                promotion_ts=prev.promotion_ts,
                status="CHAMPION_RETIRED",
            )
            self._append(retired)
        self._append(rec)
        payload = rec.to_dict()
        tmp = self.active_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.active_path)

    def get_active(self) -> ChampionRecord | None:
        if not self.active_path.exists():
            return None
        try:
            d = json.loads(self.active_path.read_text(encoding="utf-8"))
            return ChampionRecord(**d)
        except Exception:
            return None

    def clear(self) -> None:
        if self.active_path.exists():
            self.active_path.unlink(missing_ok=True)

    def history(self) -> list[ChampionRecord]:
        if not self.path.exists():
            return []
        out: list[ChampionRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(ChampionRecord(**json.loads(line)))
        return out
