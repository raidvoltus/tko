"""
Independent quantitative validation layer.

Consumes scorecards / PnL series only — does not import strategy engine for signal generation.
Prevents self-validation circularity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence

from src.evaluation.scorecard import block_bootstrap_mean_ci, build_scorecard


@dataclass
class IndependentValidationResult:
    n: int
    net_pnl: float
    max_drawdown: float
    sharpe_like: float
    mean_ci: Dict[str, float]
    pass_min_samples: bool
    notes: tuple


class IndependentValidator:
    """Evaluate precomputed PnL without strategy code paths."""

    def __init__(self, min_samples: int = 30):
        self.min_samples = min_samples

    def evaluate(self, pnls: Sequence[float], regimes: Sequence[str] | None = None) -> IndependentValidationResult:
        arr = list(pnls)
        sc = build_scorecard(arr, regimes=regimes)
        ci = block_bootstrap_mean_ci(arr, n_boot=100, seed=7)
        return IndependentValidationResult(
            n=len(arr),
            net_pnl=sc.net_pnl,
            max_drawdown=sc.max_drawdown,
            sharpe_like=sc.sharpe_like,
            mean_ci=ci,
            pass_min_samples=len(arr) >= self.min_samples,
            notes=("independent_of_strategy_engine",),
        )
