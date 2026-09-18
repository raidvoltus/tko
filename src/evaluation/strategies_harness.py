"""
Research strategy signal harness (evaluation only).

Implements paper taxonomy for offline backtests:
  - DCA (passive schedule)
  - Trend following (EMA cross)
  - Mean reversion (RSI + Bollinger)
  - Hybrid score blend

Does NOT submit orders. Used with PIT/walk-forward + friction model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np


@dataclass
class SignalSeries:
    name: str
    positions: np.ndarray  # -1, 0, +1 desired exposure fraction proxy
    n_param_trials: int = 1


def ema(x: np.ndarray, span: int) -> np.ndarray:
    alpha = 2 / (span + 1)
    out = np.empty_like(x, dtype=np.float64)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(closes), 50.0)
    if len(closes) < period + 1:
        return out
    d = np.diff(closes)
    for i in range(period, len(closes)):
        w = d[i - period : i]
        gains = np.where(w > 0, w, 0.0)
        losses = np.where(w < 0, -w, 0.0)
        ag, al = float(np.mean(gains)), float(np.mean(losses))
        out[i] = 100.0 if al == 0 else 100 - (100 / (1 + ag / al))
    return out


def bollinger(closes: np.ndarray, period: int = 20, n_std: float = 2.0):
    mid = np.zeros_like(closes)
    upper = np.zeros_like(closes)
    lower = np.zeros_like(closes)
    for i in range(len(closes)):
        j0 = max(0, i - period + 1)
        w = closes[j0 : i + 1]
        m = float(w.mean())
        s = float(w.std()) if len(w) > 1 else 0.0
        mid[i] = m
        upper[i] = m + n_std * s
        lower[i] = m - n_std * s
    return mid, upper, lower


def strategy_dca(n: int, every: int = 7) -> SignalSeries:
    pos = np.zeros(n)
    pos[:: max(every, 1)] = 1.0  # buy schedule marks
    return SignalSeries("dca", pos, n_param_trials=1)


def strategy_trend_ema(closes: Sequence[float], fast: int = 50, slow: int = 200) -> SignalSeries:
    c = np.asarray(closes, dtype=np.float64)
    ef, es = ema(c, fast), ema(c, slow)
    pos = np.where(ef > es, 1.0, 0.0)
    return SignalSeries("trend_ema", pos, n_param_trials=1)


def strategy_mean_reversion(
    closes: Sequence[float], rsi_period: int = 14, bb_period: int = 20
) -> SignalSeries:
    c = np.asarray(closes, dtype=np.float64)
    r = rsi(c, rsi_period)
    _, _, lower = bollinger(c, bb_period)
    pos = np.where((r < 30) & (c <= lower), 1.0, 0.0)
    return SignalSeries("mean_reversion", pos, n_param_trials=1)


def strategy_hybrid(
    closes: Sequence[float],
    fast: int = 20,
    slow: int = 50,
    rsi_period: int = 14,
) -> SignalSeries:
    c = np.asarray(closes, dtype=np.float64)
    t = strategy_trend_ema(c, fast, slow).positions
    m = strategy_mean_reversion(c, rsi_period).positions
    # require agreement for long
    pos = np.where((t > 0) & (m > 0), 1.0, np.where(t > 0, 0.5, 0.0))
    return SignalSeries("hybrid", pos, n_param_trials=4)


def apply_friction_pnl(
    closes: Sequence[float],
    positions: np.ndarray,
    round_trip_pct: float,
) -> np.ndarray:
    """
    Simple long-only position PnL with cost on position changes.
    positions in {0,1} (or fractional). Cost charged on abs(delta position)*price move basis
    as percent of notional proxy (return units).
    """
    c = np.asarray(closes, dtype=np.float64)
    pos = np.asarray(positions, dtype=np.float64)
    n = len(c)
    if n < 2:
        return np.zeros(0)
    ret = np.zeros(n)
    ret[1:] = np.diff(c) / c[:-1] * 100.0  # percent
    # hold PnL
    pnl = pos * ret
    # transaction cost on turnover
    turn = np.zeros(n)
    turn[1:] = np.abs(np.diff(pos))
    cost = turn * (round_trip_pct / 2.0)  # one-way approx on change
    return pnl - cost


def grid_trend_trials(closes: Sequence[float], frictions_rt: float) -> List[dict]:
    """Small grid for multiple-testing / DSR n_trials illustration."""
    trials = []
    for fast, slow in ((10, 30), (20, 50), (50, 200), (12, 26), (30, 90)):
        if slow >= len(closes):
            continue
        sig = strategy_trend_ema(closes, fast, slow)
        pnl = apply_friction_pnl(closes, sig.positions, frictions_rt)
        trials.append({"name": f"ema_{fast}_{slow}", "pnls": pnl, "params": (fast, slow)})
    return trials
