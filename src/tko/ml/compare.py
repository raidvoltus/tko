"""Champion vs Challenger apples-to-apples comparison."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from tko.ml.metrics_gates import (
    deflated_sharpe_ratio,
    evaluate_deployment_gates,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
)


@dataclass
class ModelWindowMetrics:
    model_id: str
    role: str
    n_trades: int
    net_return: float
    sharpe: float
    dsr: float
    max_drawdown: float
    profit_factor: float
    win_rate: float
    expectancy: float
    trade_frequency: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ComparisonResult:
    champion: ModelWindowMetrics
    challenger: ModelWindowMetrics
    gates: dict[str, Any]
    decision: str  # CHALLENGER_REJECTED | PROMOTION_ELIGIBLE | INSUFFICIENT_EVIDENCE
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "champion": self.champion.to_dict(),
            "challenger": self.challenger.to_dict(),
            "gates": self.gates,
            "decision": self.decision,
            "reasons": list(self.reasons),
        }


def _from_pnls(
    model_id: str,
    role: str,
    pnls: Sequence[float],
    *,
    n_trials: int = 1,
    baseline_trades: int | None = None,
) -> ModelWindowMetrics:
    eq = [100.0]
    for p in pnls:
        eq.append(eq[-1] * (1.0 + float(p)))
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    sh = sharpe_ratio(list(pnls))
    return ModelWindowMetrics(
        model_id=model_id,
        role=role,
        n_trades=n,
        net_return=float(eq[-1] / eq[0] - 1.0) if eq else 0.0,
        sharpe=sh,
        dsr=deflated_sharpe_ratio(sh, n_obs=max(2, n), n_trials=n_trials),
        max_drawdown=max_drawdown(eq),
        profit_factor=profit_factor(list(pnls)),
        win_rate=(wins / n) if n else 0.0,
        expectancy=(sum(pnls) / n) if n else 0.0,
        trade_frequency=float(n),
    )


def compare_champion_challenger(
    *,
    champion_id: str,
    challenger_id: str,
    champion_pnls: Sequence[float],
    challenger_pnls: Sequence[float],
    baseline_pnls: Sequence[float] | None = None,
    n_trials: int = 1,
    min_trades: int = 20,
    regime_ok: bool = True,
) -> ComparisonResult:
    """Identical-window comparison. Challenger must beat gates AND champion safety."""
    reasons: list[str] = []
    if len(challenger_pnls) < min_trades:
        ch = _from_pnls(challenger_id, "challenger", challenger_pnls, n_trials=n_trials)
        cp = _from_pnls(champion_id, "champion", champion_pnls, n_trials=n_trials)
        return ComparisonResult(
            champion=cp,
            challenger=ch,
            gates={},
            decision="INSUFFICIENT_EVIDENCE",
            reasons=[f"challenger_trades={len(challenger_pnls)}<{min_trades}"],
        )

    base = list(baseline_pnls) if baseline_pnls is not None else list(champion_pnls)
    base_m = _from_pnls("baseline", "baseline", base, n_trials=n_trials)
    ch_m = _from_pnls(challenger_id, "challenger", challenger_pnls, n_trials=n_trials)
    cp_m = _from_pnls(champion_id, "champion", champion_pnls, n_trials=n_trials)

    gate_report = evaluate_deployment_gates(
        baseline_sharpe=base_m.sharpe,
        ml_sharpe=ch_m.sharpe,
        baseline_mdd=base_m.max_drawdown,
        ml_mdd=ch_m.max_drawdown,
        baseline_pf=base_m.profit_factor,
        ml_pf=ch_m.profit_factor,
        baseline_trades=int(base_m.n_trades),
        ml_trades=int(ch_m.n_trades),
        dsr=ch_m.dsr,
        regime_ok=regime_ok,
        n_trials=n_trials,
    )
    # additional champion comparison: challenger sharpe not worse than champion by large margin
    # require challenger sharpe >= champion sharpe * 1.05 OR challenger expectancy higher with DSR pass
    champ_cmp_ok = ch_m.sharpe >= cp_m.sharpe * 1.05 or (
        ch_m.expectancy > cp_m.expectancy and ch_m.dsr > 0.95
    )
    if not champ_cmp_ok:
        reasons.append("challenger_not_better_than_champion")

    decision = "PROMOTION_ELIGIBLE" if gate_report.eligible and champ_cmp_ok else "CHALLENGER_REJECTED"
    if not gate_report.eligible:
        reasons.append("deployment_gates_failed")
    return ComparisonResult(
        champion=cp_m,
        challenger=ch_m,
        gates=gate_report.to_dict(),
        decision=decision,
        reasons=reasons,
    )
