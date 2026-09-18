"""Statistical promotion gate — default KEEP_CHAMPION / INSUFFICIENT_EVIDENCE."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.champion.states import PromotionDecision


@dataclass
class Scorecard:
    """Champion vs Challenger comparison metrics (all optional if not computed)."""
    sample_count: int = 0
    trade_count: int = 0
    observation_days: float = 0.0
    net_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_like: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    turnover: float = 0.0
    rejection_rate: float = 0.0
    risk_blocks: int = 0
    regime_coverage: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


@dataclass
class PromotionGateConfig:
    min_sample_count: int = 200
    min_trade_count: int = 30
    min_observation_days: float = 14.0
    max_drawdown_abs: float = 0.25
    min_sharpe_like: float = 0.0
    require_cost_adjusted: bool = True
    require_regime_robustness: bool = True
    min_regimes_covered: int = 2


class PromotionGate:
    """
    Challenger may NOT promote on raw return alone.
    Default: KEEP_CHAMPION / INSUFFICIENT_EVIDENCE.
    """

    def __init__(self, cfg: Optional[PromotionGateConfig] = None):
        self.cfg = cfg or PromotionGateConfig()

    def evaluate(
        self,
        champion: Scorecard,
        challenger: Scorecard,
        cost_adjusted: bool = False,
        reproducible: bool = True,
        risk_limits_unchanged: bool = True,
    ) -> Dict[str, Any]:
        reasons: List[str] = []

        if not risk_limits_unchanged:
            return self._out(PromotionDecision.REJECT, ["RISK_LIMITS_WOULD_CHANGE"])

        if not reproducible:
            return self._out(PromotionDecision.REJECT, ["NOT_REPRODUCIBLE"])

        if self.cfg.require_cost_adjusted and not cost_adjusted:
            reasons.append("EDGE_NOT_COST_ADJUSTED")
            return self._out(PromotionDecision.INSUFFICIENT_EVIDENCE, reasons)

        if challenger.sample_count < self.cfg.min_sample_count:
            reasons.append(
                f"INSUFFICIENT_SAMPLES:{challenger.sample_count}<{self.cfg.min_sample_count}"
            )
        if challenger.trade_count < self.cfg.min_trade_count:
            reasons.append(
                f"INSUFFICIENT_TRADES:{challenger.trade_count}<{self.cfg.min_trade_count}"
            )
        if challenger.observation_days < self.cfg.min_observation_days:
            reasons.append(
                f"INSUFFICIENT_WINDOW:{challenger.observation_days}<{self.cfg.min_observation_days}"
            )

        if reasons:
            return self._out(PromotionDecision.INSUFFICIENT_EVIDENCE, reasons)

        if abs(challenger.max_drawdown) > self.cfg.max_drawdown_abs:
            return self._out(
                PromotionDecision.REJECT,
                [f"DRAWDOWN_BREACH:{challenger.max_drawdown}"],
            )

        if challenger.sharpe_like < self.cfg.min_sharpe_like:
            return self._out(
                PromotionDecision.REJECT,
                [f"SHARPE_BELOW_FLOOR:{challenger.sharpe_like}"],
            )

        if self.cfg.require_regime_robustness:
            covered = len([k for k, v in challenger.regime_coverage.items() if v > 0])
            if covered < self.cfg.min_regimes_covered:
                return self._out(
                    PromotionDecision.INSUFFICIENT_EVIDENCE,
                    [f"REGIME_COVERAGE:{covered}<{self.cfg.min_regimes_covered}"],
                )

        # Relative improvement required vs champion (net PnL and drawdown)
        if challenger.net_pnl <= champion.net_pnl:
            return self._out(
                PromotionDecision.KEEP_CHAMPION,
                ["NO_NET_PNL_IMPROVEMENT"],
            )
        if abs(challenger.max_drawdown) > abs(champion.max_drawdown) * 1.15:
            return self._out(
                PromotionDecision.KEEP_CHAMPION,
                ["DRAWDOWN_WORSE_THAN_CHAMPION"],
            )

        # Even if metrics look better, require explicit operator confirmation layer
        # Automatic PROMOTE is never the default in this gate without all checks — still
        # return ELIGIBLE signal via decision PROMOTE only when all hard gates pass.
        return self._out(
            PromotionDecision.PROMOTE,
            ["ALL_GATES_PASSED"],
            eligible=True,
        )

    def _out(
        self,
        decision: PromotionDecision,
        reasons: List[str],
        eligible: bool = False,
    ) -> Dict[str, Any]:
        return {
            "decision": decision.value,
            "promotion_eligible": eligible and decision == PromotionDecision.PROMOTE,
            "promotion_reason": ";".join(reasons) if reasons else decision.value,
            "default_policy": "KEEP_CHAMPION_WHEN_UNCERTAIN",
        }
