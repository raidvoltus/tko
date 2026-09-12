"""DSR, baseline comparison, and objective deployment gates."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GateResult:
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str = ""


@dataclass
class DeploymentGateReport:
    gates: list[GateResult] = field(default_factory=list)
    eligible: bool = False
    decision: str = "REJECTED"  # REJECTED | SHADOW_ELIGIBLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "decision": self.decision,
            "gates": [
                {
                    "name": g.name,
                    "passed": g.passed,
                    "value": g.value,
                    "threshold": g.threshold,
                    "detail": g.detail,
                }
                for g in self.gates
            ],
        }


def sharpe_ratio(returns: Sequence[float], *, eps: float = 1e-12) -> float:
    if len(returns) < 2:
        return 0.0
    mu = sum(returns) / len(returns)
    var = sum((r - mu) ** 2 for r in returns) / max(1, len(returns) - 1)
    sd = math.sqrt(max(0.0, var))
    return mu / max(eps, sd) * math.sqrt(len(returns))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    n_obs: int,
    n_trials: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """Approximate DSR (Bailey & López de Prado). Returns probability-like score in [0,1].

    Higher is better; gate uses DSR > 0.95.
    """
    if n_obs < 2 or n_trials < 1:
        return 0.0
    # expected max SR under null ~ sqrt(2*log(n_trials))
    e_max = math.sqrt(max(0.0, 2.0 * math.log(max(2, n_trials))))
    # variance of SR
    sr = observed_sharpe
    v = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / max(1, n_obs - 1)
    v = max(1e-12, v)
    # z-score vs expected max
    z = (sr - e_max) / math.sqrt(v)
    # standard normal CDF
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def max_drawdown(equity: Sequence[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for x in equity:
        peak = max(peak, x)
        if peak > 0:
            mdd = max(mdd, (peak - x) / peak)
    return mdd


def profit_factor(pnls: Sequence[float]) -> float:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl < 1e-12:
        return 10.0 if gp > 0 else 0.0
    return gp / gl


def evaluate_deployment_gates(
    *,
    baseline_sharpe: float,
    ml_sharpe: float,
    baseline_mdd: float,
    ml_mdd: float,
    baseline_pf: float,
    ml_pf: float,
    baseline_trades: int,
    ml_trades: int,
    dsr: float,
    regime_ok: bool = True,
    n_trials: int = 1,
) -> DeploymentGateReport:
    """Hard objective gates from research spec. All must pass → SHADOW_ELIGIBLE."""
    gates: list[GateResult] = []
    gates.append(
        GateResult("DSR", dsr > 0.95, dsr, 0.95, f"n_trials={n_trials}")
    )
    # Sharpe improvement >= 10%
    sharpe_ok = ml_sharpe >= baseline_sharpe * 1.10 if baseline_sharpe > 0 else ml_sharpe > 0
    gates.append(
        GateResult(
            "SHARPE_IMPROVEMENT",
            sharpe_ok,
            ml_sharpe,
            baseline_sharpe * 1.10 if baseline_sharpe > 0 else 0.0,
            f"baseline={baseline_sharpe:.4f}",
        )
    )
    gates.append(
        GateResult(
            "MAX_DRAWDOWN",
            ml_mdd <= baseline_mdd + 1e-12,
            ml_mdd,
            baseline_mdd,
            f"baseline_mdd={baseline_mdd:.4f}",
        )
    )
    pf_ok = ml_pf >= baseline_pf * 1.05 if baseline_pf > 0 else ml_pf > 0
    gates.append(
        GateResult(
            "PROFIT_FACTOR",
            pf_ok,
            ml_pf,
            baseline_pf * 1.05 if baseline_pf > 0 else 0.0,
            f"baseline_pf={baseline_pf:.4f}",
        )
    )
    freq_ok = ml_trades >= math.ceil(0.5 * max(0, baseline_trades))
    gates.append(
        GateResult(
            "TRADE_FREQUENCY",
            freq_ok,
            float(ml_trades),
            0.5 * baseline_trades,
            f"baseline_trades={baseline_trades}",
        )
    )
    gates.append(GateResult("REGIME_ROBUSTNESS", regime_ok, 1.0 if regime_ok else 0.0, 1.0))
    eligible = all(g.passed for g in gates)
    return DeploymentGateReport(
        gates=gates,
        eligible=eligible,
        decision="SHADOW_ELIGIBLE" if eligible else "REJECTED",
    )
