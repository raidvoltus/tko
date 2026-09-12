"""Market-data freshness gate — fail-closed for BUY entry (Finding 6).

SELL is intentionally not gated here unless a caller opts in; entry/BUY must always pass.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    ok: bool
    reason: str
    age_sec: float | None = None


def _as_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def validate_market_data_freshness(
    *,
    timestamp: Any,
    max_age_sec: float,
    now: float | None = None,
    allow_future_skew_sec: float = 5.0,
) -> FreshnessResult:
    """Validate market data timestamp for BUY authorization.

    Fail-closed rules:
    - missing / non-numeric / NaN / inf timestamp → BLOCK
    - age > max_age_sec → BLOCK (stale)
    - timestamp too far in the future → BLOCK (clock/data anomaly)
    - max_age_sec <= 0 disables the gate (explicit opt-out)
    """
    max_age = float(max_age_sec)
    if max_age <= 0:
        return FreshnessResult(True, "freshness_disabled", age_sec=None)

    ts = _as_float(timestamp)
    if ts is None:
        return FreshnessResult(False, "market data timestamp missing or non-finite")

    if ts > 1e12:
        ts = ts / 1000.0
    if ts <= 0:
        return FreshnessResult(False, "market data timestamp non-positive")

    now_ts = float(now if now is not None else time.time())
    age = now_ts - ts
    if age < -float(allow_future_skew_sec):
        return FreshnessResult(False, f"market data timestamp in future age={age:.3f}s", age_sec=age)

    if max_age > 0 and age > max_age:
        return FreshnessResult(False, f"market data stale age={age:.3f}s max={max_age:.3f}s", age_sec=age)

    return FreshnessResult(True, "fresh", age_sec=max(0.0, age))
