"""Triple Barrier Method labels for primary BUY meta-labeling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class TbmConfig:
    pt_mult: float = 1.0   # upper barrier = pt_mult * vol
    sl_mult: float = 1.0   # lower barrier = sl_mult * vol
    max_horizon: int = 12  # vertical barrier in bars
    vol_lookback: int = 20
    min_vol: float = 1e-6


@dataclass(frozen=True, slots=True)
class TbmLabel:
    event_index: int
    label: int          # +1 upper, -1 lower, 0 vertical/neutral
    horizon_used: int
    barrier: str        # "pt" | "sl" | "vb"


def _realized_vol(closes: Sequence[float], end_idx: int, lookback: int) -> float:
    """Causal vol at end_idx using closes[end_idx-lookback : end_idx+1]."""
    start = max(1, end_idx - lookback + 1)
    rets: list[float] = []
    for i in range(start, end_idx + 1):
        c0, c1 = float(closes[i - 1]), float(closes[i])
        if c0 > 0 and c1 > 0:
            rets.append(math.log(c1 / c0))
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    return math.sqrt(max(0.0, var))


def triple_barrier_labels(
    closes: Sequence[float],
    event_indices: Sequence[int],
    *,
    config: TbmConfig | None = None,
) -> list[TbmLabel]:
    """Label each event using only future path AFTER the event (for training labels).

    Barrier widths use volatility estimated at/before event time (causal).
    Label values use path after event (standard supervised target construction).
    Overlapping horizons must be handled by purged CV, not here.
    """
    cfg = config or TbmConfig()
    out: list[TbmLabel] = []
    n = len(closes)
    for ei in event_indices:
        if ei < 0 or ei >= n - 1:
            continue
        px = float(closes[ei])
        if px <= 0:
            continue
        vol = max(cfg.min_vol, _realized_vol(closes, ei, cfg.vol_lookback))
        upper = px * math.exp(cfg.pt_mult * vol * math.sqrt(cfg.max_horizon))
        lower = px * math.exp(-cfg.sl_mult * vol * math.sqrt(cfg.max_horizon))
        label = 0
        barrier = "vb"
        horizon = 0
        end = min(n - 1, ei + cfg.max_horizon)
        for j in range(ei + 1, end + 1):
            horizon = j - ei
            c = float(closes[j])
            if c >= upper:
                label = 1
                barrier = "pt"
                break
            if c <= lower:
                label = -1
                barrier = "sl"
                break
        out.append(TbmLabel(event_index=ei, label=label, horizon_used=horizon, barrier=barrier))
    return out


def meta_label_from_tbm(tbm_labels: Sequence[TbmLabel]) -> list[tuple[int, int]]:
    """Map TBM to meta-label for BUY success: +1 success (pt), 0 otherwise."""
    return [(t.event_index, 1 if t.label == 1 else 0) for t in tbm_labels]
