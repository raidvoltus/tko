"""
Advanced evaluation metrics for quantitative strategy research.

- Sortino, Calmar
- Walk-Forward Efficiency (WFE)
- Deflated Sharpe Ratio (Bailey & López de Prado style approximation)

CPU-only. Evaluation layer only — no order authority.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Sequence

import numpy as np


def _returns(pnls: Sequence[float]) -> np.ndarray:
    return np.asarray(list(pnls), dtype=np.float64)


def sharpe_like(pnls: Sequence[float], periods_per_year: float = 252.0) -> float:
    r = _returns(pnls)
    if r.size < 2:
        return 0.0
    mu, sd = float(r.mean()), float(r.std())
    if sd < 1e-12:
        return 0.0
    return mu / sd * math.sqrt(min(r.size, periods_per_year))


def sortino_ratio(pnls: Sequence[float], periods_per_year: float = 252.0) -> float:
    r = _returns(pnls)
    if r.size < 2:
        return 0.0
    downside = r[r < 0]
    dd = float(downside.std()) if downside.size else 0.0
    if dd < 1e-12:
        return 0.0 if float(r.mean()) <= 0 else 10.0
    return float(r.mean()) / dd * math.sqrt(min(r.size, periods_per_year))


def calmar_ratio(pnls: Sequence[float]) -> float:
    r = _returns(pnls)
    if r.size == 0:
        return 0.0
    equity = np.cumsum(r)
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    mdd = float(abs(dd.min())) if dd.size else 0.0
    if mdd < 1e-12:
        return 0.0 if float(equity[-1]) <= 0 else 10.0
    return float(equity[-1]) / mdd


def walk_forward_efficiency(oos_pnl: float, is_pnl: float) -> float:
    """
    WFE ≈ OOS performance / IS performance (profit retention).
    Paper disqualifies WFE < 0.50; strong if > 0.70.
    """
    if abs(is_pnl) < 1e-12:
        return 0.0
    return float(oos_pnl / is_pnl)


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    n_observations: int,
    skew: float = 0.0,
    excess_kurtosis: float = 0.0,
    variance_sharpe: Optional[float] = None,
) -> float:
    """
    Approximate Deflated Sharpe Ratio (Bailey & López de Prado).

    Returns probability-like score in [0,1] that observed SR is not due to
    selection bias given n_trials. Higher = more credible.

    This is a practical approximation suitable for CPU research harness;
    not a claim of live alpha.
    """
    if n_observations < 5 or n_trials < 1:
        return 0.0
    # Expected max SR under null of independent trials (Euler-Mascheroni approx)
    # E[max Z] ≈ (1-γ)*Φ^{-1}(1-1/N) + γ*Φ^{-1}(1-1/(N*e))
    gamma = 0.5772156649
    N = max(int(n_trials), 1)
    # inverse normal via erfinv approximation
    def _ninv(p: float) -> float:
        p = min(max(p, 1e-12), 1 - 1e-12)
        # Beasley-Springer-Moro-ish simple
        return float(math.sqrt(2) * _erfinv(2 * p - 1))

    def _erfinv(x: float) -> float:
        # numerical recipe approximation
        a = 0.147
        ln = math.log(1 - x * x)
        s = (2 / (math.pi * a) + ln / 2)
        return float(math.copysign(1, x) * math.sqrt(math.sqrt(s * s - ln / a) - s))

    emax = (1 - gamma) * _ninv(1 - 1 / N) + gamma * _ninv(1 - 1 / (N * math.e))
    # SR variance under non-normality (Lo 2002 / BLP)
    if variance_sharpe is None:
        variance_sharpe = (
            1.0
            + 0.5 * observed_sharpe**2
            - skew * observed_sharpe
            + (excess_kurtosis / 4.0) * observed_sharpe**2
        ) / max(n_observations, 1)
    variance_sharpe = max(float(variance_sharpe), 1e-12)
    # z-score of (SR - E[max SR under null scaled])
    # under null true SR=0, expected max ≈ emax * sqrt(var)
    null_max = emax * math.sqrt(variance_sharpe)
    z = (observed_sharpe - null_max) / math.sqrt(variance_sharpe)
    # Φ(z)
    dsr = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return float(min(max(dsr, 0.0), 1.0))


def moments(pnls: Sequence[float]) -> Dict[str, float]:
    r = _returns(pnls)
    if r.size < 3:
        return {"mean": 0.0, "std": 0.0, "skew": 0.0, "excess_kurtosis": 0.0}
    mu = float(r.mean())
    sd = float(r.std()) + 1e-12
    z = (r - mu) / sd
    skew = float(np.mean(z**3))
    kurt = float(np.mean(z**4) - 3.0)
    return {"mean": mu, "std": sd, "skew": skew, "excess_kurtosis": kurt}


def research_scorecard(
    pnls: Sequence[float],
    *,
    n_trials: int = 1,
    is_pnl: Optional[float] = None,
    oos_pnl: Optional[float] = None,
) -> Dict[str, float]:
    """Bundle Sortino/Calmar/DSR/WFE for research reports."""
    r = _returns(pnls)
    m = moments(r)
    sr = sharpe_like(r)
    dsr = deflated_sharpe_ratio(
        sr,
        n_trials=n_trials,
        n_observations=max(len(r), 1),
        skew=m["skew"],
        excess_kurtosis=m["excess_kurtosis"],
    )
    out = {
        "sharpe_like": sr,
        "sortino": sortino_ratio(r),
        "calmar": calmar_ratio(r),
        "dsr": dsr,
        "n": float(len(r)),
        "n_trials": float(n_trials),
        "skew": m["skew"],
        "excess_kurtosis": m["excess_kurtosis"],
        "net_pnl": float(r.sum()) if r.size else 0.0,
    }
    if is_pnl is not None and oos_pnl is not None:
        out["wfe"] = walk_forward_efficiency(oos_pnl, is_pnl)
    return out
