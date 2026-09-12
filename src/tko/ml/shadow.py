"""Shadow evaluation — predictions logged, zero execution authority."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class ShadowRecord:
    timestamp: float
    model_version: str
    primary_signal: str
    confidence: float
    would_filter: bool
    prediction: int
    actual_outcome: int | None = None
    notes: str = ""


class ShadowLogger:
    """Append-only shadow predictions. Never calls exchange/execution."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, record: ShadowRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record)) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out


def shadow_decision(
    *,
    primary_signal: str,
    confidence: float,
    min_confidence: float,
    model_version: str,
    logger: ShadowLogger | None = None,
) -> dict[str, Any]:
    """Compute would_filter for BUY; SELL never filtered by this path."""
    would_filter = False
    prediction = 0
    if primary_signal.upper() == "BUY":
        prediction = 1 if confidence >= min_confidence else 0
        would_filter = confidence < min_confidence
    rec = ShadowRecord(
        timestamp=time.time(),
        model_version=model_version,
        primary_signal=primary_signal,
        confidence=float(confidence),
        would_filter=would_filter,
        prediction=prediction,
    )
    if logger is not None:
        logger.log(rec)
    return asdict(rec)
