"""Champion vs Challenger scorecard builders — empirical evidence only."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

import numpy as np

from src.champion.promotion import Scorecard


@dataclass
class TradeRecord:
    pnl: float
    regime: str = "UNKNOWN"
    rejected: bool = False
    risk_blocked: bool = False


def build_scorecard(
    pnls: Sequence[float],
    regimes: Optional[Sequence[str]] = None,
    observation_days: float = 0.0,
    risk_blocks: int = 0,
    rejection_rate: float = 0.0,
) -> Scorecard:
    """Build scorecard from realized PnL series (point-in-time evaluated)."""
    arr = np.asarray(list(pnls), dtype=np.float64)
    n = int(arr.size)
    if n == 0:
        return Scorecard(observation_days=observation_days, risk_blocks=risk_blocks)

    equity = np.cumsum(arr)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    max_dd = float(abs(dd.min())) if dd.size else 0.0
    mean = float(arr.mean())
    std = float(arr.std()) + 1e-12
    sharpe_like = mean / std * np.sqrt(min(n, 252))
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    win_rate = float((arr > 0).mean()) if n else 0.0
    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(abs(losses.sum())) if losses.size else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    expectancy = mean

    regime_coverage: Dict[str, float] = {}
    if regimes is not None and len(regimes) == n:
        for r in set(regimes):
            regime_coverage[str(r)] = float(sum(1 for x in regimes if x == r) / n)

    return Scorecard(
        sample_count=n,
        trade_count=n,
        observation_days=observation_days,
        net_pnl=float(arr.sum()),
        max_drawdown=max_dd,
        sharpe_like=float(sharpe_like),
        win_rate=win_rate,
        profit_factor=float(profit_factor) if np.isfinite(profit_factor) else 0.0,
        expectancy=float(expectancy),
        turnover=float(n),
        rejection_rate=rejection_rate,
        risk_blocks=risk_blocks,
        regime_coverage=regime_coverage,
        notes=["built_from_realized_pnl"],
    )


def block_bootstrap_mean_ci(
    pnls: Sequence[float],
    block_size: int = 10,
    n_boot: int = 200,
    alpha: float = 0.05,
    seed: int = 42,
) -> Dict[str, float]:
    """
    Block bootstrap CI for mean PnL (uncertainty estimate).
    Not a full multiple-testing correction — reports uncertainty only.
    """
    arr = np.asarray(list(pnls), dtype=np.float64)
    n = len(arr)
    if n < block_size or n == 0:
        return {"mean": float(arr.mean()) if n else 0.0, "ci_low": 0.0, "ci_high": 0.0, "n_boot": 0}

    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block_size))
    means = []
    for _ in range(n_boot):
        starts = rng.integers(0, max(1, n - block_size + 1), size=n_blocks)
        sample = np.concatenate([arr[s : s + block_size] for s in starts])[:n]
        means.append(float(sample.mean()))
    means_a = np.sort(np.asarray(means))
    lo = float(np.quantile(means_a, alpha / 2))
    hi = float(np.quantile(means_a, 1 - alpha / 2))
    return {
        "mean": float(arr.mean()),
        "ci_low": lo,
        "ci_high": hi,
        "n_boot": n_boot,
        "block_size": block_size,
        "alpha": alpha,
    }


def calibration_reliability(
    scores: Sequence[float],
    outcomes: Sequence[float],
    n_bins: int = 10,
) -> Dict[str, float]:
    """
    Simple reliability diagram stats for scores in [0,1] vs binary outcomes.
    Returns ECE (expected calibration error). Does NOT claim model is calibrated.
    """
    s = np.asarray(list(scores), dtype=np.float64)
    y = np.asarray(list(outcomes), dtype=np.float64)
    if s.size == 0 or s.size != y.size:
        return {"ece": 1.0, "n": 0, "calibrated": False}
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (s >= bins[i]) & (s < bins[i + 1] if i < n_bins - 1 else s <= bins[i + 1])
        if not np.any(mask):
            continue
        conf = float(s[mask].mean())
        acc = float(y[mask].mean())
        ece += abs(conf - acc) * (mask.sum() / s.size)
    return {"ece": float(ece), "n": int(s.size), "calibrated": False}
