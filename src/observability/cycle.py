"""OBSERVABILITY - cycle journal, metrics JSONL, replay support."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CycleJournal:
    def __init__(self, audit_dir: Path):
        self.audit_dir = Path(audit_dir)
        self.cycles_dir = self.audit_dir / "cycles"
        self.cycles_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_path = self.audit_dir / "metrics.jsonl"

    def write_cycle(self, cycle: Dict[str, Any]) -> Path:
        cid = cycle.get("cycle_id", "unknown")
        path = self.cycles_dir / f"{cid}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cycle, f, indent=2, default=str)
        tmp.replace(path)
        # also append summary metrics
        summary = {
            "cycle_id": cid,
            "timestamp": cycle.get("timestamp", time.time()),
            "hold": cycle.get("hold"),
            "reason": cycle.get("reason"),
            "symbols_scanned": cycle.get("symbols_scanned", 0),
            "latency_ms": cycle.get("latency_ms", {}),
        }
        with open(self.metrics_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, default=str) + "\n")
        return path

    def load_cycle(self, cycle_id: str) -> Optional[Dict[str, Any]]:
        path = self.cycles_dir / f"{cycle_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


class DataQualityGate:
    """Freshness / gap / anomaly checks — fail-closed for new orders."""

    def __init__(self, max_stale_sec: float = 45.0, max_jump_pct: float = 15.0):
        self.max_stale_sec = max_stale_sec
        self.max_jump_pct = max_jump_pct
        self.last_price: Dict[str, float] = {}
        self.last_ts: Dict[str, float] = {}

    def update(self, symbol: str, price: float, ts: Optional[float] = None) -> Dict[str, Any]:
        now = ts or time.time()
        issues = []
        prev = self.last_price.get(symbol)
        if prev and prev > 0:
            jump = abs(price - prev) / prev * 100.0
            if jump > self.max_jump_pct:
                issues.append(f"price_jump_{jump:.1f}pct")
        age = now - self.last_ts.get(symbol, now)
        if self.last_ts.get(symbol) and age > self.max_stale_sec:
            issues.append("stale")
        self.last_price[symbol] = price
        self.last_ts[symbol] = now
        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "age_sec": age if self.last_ts.get(symbol) else 0,
        }

    def is_fresh(self, symbol: str) -> bool:
        ts = self.last_ts.get(symbol)
        if ts is None:
            return False
        return (time.time() - ts) <= self.max_stale_sec
