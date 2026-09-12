"""Symmetric CUSUM event sampling — causal, deterministic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class CusumConfig:
    threshold: float = 0.02  # log-return units
    min_bars_between: int = 1


@dataclass(frozen=True, slots=True)
class CusumEvent:
    index: int
    timestamp_ms: int
    side: int  # +1 up, -1 down
    level: float


def symmetric_cusum_events(
    closes: Sequence[float],
    timestamps_ms: Sequence[int],
    *,
    config: CusumConfig | None = None,
) -> list[CusumEvent]:
    """Symmetric CUSUM on log returns. Events only use data <= t.

    Deterministic: same inputs + config → same events.
    """
    cfg = config or CusumConfig()
    thr = float(cfg.threshold)
    if thr <= 0 or len(closes) < 2:
        return []
    events: list[CusumEvent] = []
    s_pos = 0.0
    s_neg = 0.0
    last_idx = -10**9
    import math
    for i in range(1, len(closes)):
        c0, c1 = float(closes[i - 1]), float(closes[i])
        if c0 <= 0 or c1 <= 0:
            s_pos = s_neg = 0.0
            continue
        r = math.log(c1 / c0)
        s_pos = max(0.0, s_pos + r)
        s_neg = min(0.0, s_neg + r)
        side = 0
        if s_pos >= thr:
            side = 1
            s_pos = 0.0
            s_neg = 0.0
        elif s_neg <= -thr:
            side = -1
            s_pos = 0.0
            s_neg = 0.0
        if side != 0 and (i - last_idx) >= int(cfg.min_bars_between):
            ts = int(timestamps_ms[i]) if i < len(timestamps_ms) else 0
            events.append(CusumEvent(index=i, timestamp_ms=ts, side=side, level=float(closes[i])))
            last_idx = i
    return events
