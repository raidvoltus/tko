"""Shadow observation window — fail-closed until min evidence."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ObservationConfig:
    min_samples: int = 30
    min_duration_sec: float = 0.0  # 0 = sample-count only
    max_disagreement_rate: float = 1.0  # informational


@dataclass
class ObservationEvent:
    ts: float
    symbol: str
    primary_signal: str
    champion_pred: int
    challenger_pred: int
    champion_conf: float
    challenger_conf: float
    disagreement: bool
    regime: str = ""
    market_ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObservationWindow:
    def __init__(self, path: Path, config: ObservationConfig | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config or ObservationConfig()
        self.started_ts = time.time()

    def log(self, event: ObservationEvent) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict()) + "\n")

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def status(self) -> dict[str, Any]:
        ev = self.events()
        n = len(ev)
        disag = sum(1 for e in ev if e.get("disagreement"))
        elapsed = time.time() - self.started_ts
        enough_n = n >= int(self.config.min_samples)
        enough_t = elapsed >= float(self.config.min_duration_sec)
        ready = enough_n and enough_t
        return {
            "n_samples": n,
            "disagreements": disag,
            "disagreement_rate": (disag / n) if n else 0.0,
            "elapsed_sec": elapsed,
            "ready": ready,
            "decision": "READY" if ready else "INSUFFICIENT_EVIDENCE",
        }
