"""
Structured operational metrics for cycles / risk / signals.

Bounded in-memory ring; no secrets. Complements RiskEngine event trail.
"""
from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Deque, Dict


@dataclass
class CycleMetric:
    ts: float
    cycle_id: str
    mode: str
    regime: str
    action: str
    strategy_composite: float
    model_score: float
    net_edge_pct: float
    risk_allowed: bool
    risk_reason: str
    risk_mode: str
    state_health: str
    latency_ms: float = 0.0


class MetricsRegistry:
    def __init__(self, maxlen: int = 500):
        self._cycles: Deque[CycleMetric] = deque(maxlen=maxlen)
        self._counters: Dict[str, int] = {}

    def incr(self, name: str, n: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + n

    def record_cycle(self, m: CycleMetric) -> None:
        self._cycles.append(m)
        self.incr("cycles_total")
        if m.risk_allowed:
            self.incr("risk_allow")
        else:
            self.incr("risk_deny")
        self.incr(f"action_{m.action}")

    def snapshot(self) -> Dict[str, Any]:
        return {
            "ts": time.time(),
            "counters": dict(self._counters),
            "recent_cycles": [asdict(c) for c in list(self._cycles)[-20:]],
        }

    def to_jsonl_line(self, m: CycleMetric) -> str:
        return json.dumps(asdict(m), separators=(",", ":"))
