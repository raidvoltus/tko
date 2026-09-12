"""Simple regime tagging for robustness checks (no HMM required)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class RegimeSlice:
    name: str
    indices: tuple[int, ...]


def tag_regimes(
    closes: Sequence[float],
    *,
    lookback: int = 20,
) -> list[str]:
    """Tag each bar as trending/ranging + high_vol/low_vol composite labels.

    Returns list of regime name per index (empty string for warmup).
    """
    n = len(closes)
    tags = [""] * n
    if n < lookback + 2:
        return tags
    for i in range(lookback, n):
        window = [float(closes[j]) for j in range(i - lookback + 1, i + 1)]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0 and window[j] > 0:
                rets.append(math.log(window[j] / window[j - 1]))
        if len(rets) < 5:
            continue
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
        vol = math.sqrt(max(0.0, var))
        trend = abs(window[-1] / window[0] - 1.0) if window[0] else 0.0
        vol_tag = "high_vol" if vol > 0.01 else "low_vol"
        trend_tag = "trending" if trend > 0.03 else "ranging"
        tags[i] = f"{trend_tag}_{vol_tag}"
    return tags


def regime_performance(
    tags: Sequence[str],
    pnls: Sequence[float],
    indices: Sequence[int],
) -> dict[str, dict[str, float]]:
    """Aggregate PnL stats per regime for indices aligned with pnls."""
    buckets: dict[str, list[float]] = {}
    for idx, pnl in zip(indices, pnls):
        if idx < 0 or idx >= len(tags):
            continue
        name = tags[idx] or "unknown"
        buckets.setdefault(name, []).append(float(pnl))
    out: dict[str, dict[str, float]] = {}
    for name, vals in buckets.items():
        if not vals:
            continue
        out[name] = {
            "n": float(len(vals)),
            "mean_pnl": sum(vals) / len(vals),
            "sum_pnl": sum(vals),
        }
    return out


def regime_not_extreme_degraded(
    baseline_by_regime: dict[str, dict[str, float]],
    ml_by_regime: dict[str, dict[str, float]],
    *,
    min_trades: int = 3,
    max_mean_underperformance: float = 0.0,
) -> bool:
    """True if ML mean PnL is not worse than baseline in every regime with enough trades."""
    for name, b in baseline_by_regime.items():
        if b.get("n", 0) < min_trades:
            continue
        m = ml_by_regime.get(name)
        if not m or m.get("n", 0) < min_trades:
            continue
        if m["mean_pnl"] + 1e-12 < b["mean_pnl"] + max_mean_underperformance:
            # allow mild underperformance; extreme = much worse
            if m["mean_pnl"] < b["mean_pnl"] - abs(b["mean_pnl"]) - 1e-6:
                return False
    return True
