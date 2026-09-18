"""
Walk-forward evaluation runner — fills Champion/Challenger scorecards.

Point-in-time: at bar t, only features available at t are used for signal.
Forward labels are for analysis only, not as model inputs at t.

Does not submit orders. Cost model is explicit and heuristic until calibrated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from src.champion.promotion import Scorecard
from src.decision.edge import cost_adjusted_edge, strategy_score_to_gross_edge
from src.decision.strategies import StrategyEngine
from src.evaluation.pit_dataset import PITDataset
from src.evaluation.scorecard import block_bootstrap_mean_ci, build_scorecard
from src.evaluation.walk_forward import WalkForwardSplit, generate_walk_forward_splits


@dataclass
class FoldResult:
    fold: int
    split: WalkForwardSplit
    pnls: List[float]
    regimes: List[str]
    signals: List[str]
    n_test: int
    scorecard: Scorecard
    bootstrap_ci: Dict[str, float] = field(default_factory=dict)


@dataclass
class WalkForwardReport:
    symbol: str
    feature_schema_hash: str
    folds: List[FoldResult]
    aggregate: Scorecard
    aggregate_bootstrap_ci: Dict[str, float]
    cost_model: Dict[str, float]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "feature_schema_hash": self.feature_schema_hash,
            "n_folds": len(self.folds),
            "aggregate": {
                "sample_count": self.aggregate.sample_count,
                "trade_count": self.aggregate.trade_count,
                "net_pnl": self.aggregate.net_pnl,
                "max_drawdown": self.aggregate.max_drawdown,
                "sharpe_like": self.aggregate.sharpe_like,
                "win_rate": self.aggregate.win_rate,
                "regime_coverage": self.aggregate.regime_coverage,
            },
            "bootstrap_ci": self.aggregate_bootstrap_ci,
            "cost_model": self.cost_model,
            "notes": self.notes,
            "folds": [
                {
                    "fold": f.fold,
                    "n_test": f.n_test,
                    "net_pnl": f.scorecard.net_pnl,
                    "max_drawdown": f.scorecard.max_drawdown,
                    "bootstrap_ci": f.bootstrap_ci,
                }
                for f in self.folds
            ],
        }


@dataclass
class CostModel:
    """Per-trade cost assumptions (percent). Not calibrated expected edge."""

    fee_pct: float = 0.1
    spread_pct: float = 0.05
    slippage_pct: float = 0.05
    impact_pct: float = 0.02
    exec_risk_pct: float = 0.03
    vol_risk_pct: float = 0.05

    def total_pct(self) -> float:
        return (
            self.fee_pct
            + self.spread_pct
            + self.slippage_pct
            + self.impact_pct
            + self.exec_risk_pct
            + self.vol_risk_pct
        )

    def as_dict(self) -> Dict[str, float]:
        return {
            "fee_pct": self.fee_pct,
            "spread_pct": self.spread_pct,
            "slippage_pct": self.slippage_pct,
            "impact_pct": self.impact_pct,
            "exec_risk_pct": self.exec_risk_pct,
            "vol_risk_pct": self.vol_risk_pct,
            "total_pct": self.total_pct(),
            "calibrated": False,
        }


class WalkForwardRunner:
    """
    Evaluate StrategyEngine confluence on PIT test folds.

    Signal at t: strategy composite from closes[:t+1].
    Realized gross ≈ composite-mapped edge applied to fwd_ret_1 direction proxy;
    net = after cost model.

    This is an evaluation harness, not a live trading path.
    """

    def __init__(
        self,
        cost: Optional[CostModel] = None,
        min_bars_history: int = 40,
        entry_threshold: float = 0.15,
    ):
        self.cost = cost or CostModel()
        self.min_bars_history = min_bars_history
        self.entry_threshold = entry_threshold
        self.strategies = StrategyEngine()

    def evaluate_fold(self, ds: PITDataset, split: WalkForwardSplit) -> FoldResult:
        # Test region only for scorecard (out-of-sample)
        test_pnls: List[float] = []
        test_regimes: List[str] = []
        test_signals: List[str] = []

        for t in range(split.valid_end, split.test_end):
            if t < self.min_bars_history:
                continue
            # PIT closes available up to t inclusive
            closes = ds.close[: t + 1]
            volumes = ds.volume[: t + 1]
            rsi = float(ds.features["rsi_14"][t]) if "rsi_14" in ds.features else 50.0
            scores = self.strategies.evaluate(closes, volumes=volumes, rsi=rsi)
            regime = scores.regime
            comp = scores.composite

            # Position intent from confluence (evaluation only)
            if abs(comp) < self.entry_threshold or regime in ("UNKNOWN", "VOLATILE"):
                sig = "FLAT"
                pnl = 0.0
            else:
                sig = "LONG" if comp > 0 else "SHORT"
                # Gross edge heuristic from composite; apply sign vs realized fwd ret if present
                gross = strategy_score_to_gross_edge(comp)
                edge = cost_adjusted_edge(
                    abs(gross),
                    fee_pct=self.cost.fee_pct,
                    spread_pct=self.cost.spread_pct,
                    slippage_pct=self.cost.slippage_pct,
                    impact_pct=self.cost.impact_pct,
                    exec_risk_pct=self.cost.exec_risk_pct,
                    vol_risk_pct=self.cost.vol_risk_pct,
                    source="wf_heuristic",
                    calibrated=False,
                )
                fwd = ds.labels.get("fwd_ret_1")
                if fwd is not None and t < len(fwd) and np.isfinite(fwd[t]):
                    # signed PnL: long benefits from +fwd, short from -fwd; scaled by |net edge|
                    direction = 1.0 if sig == "LONG" else -1.0
                    # convert log ret to pct and scale by edge magnitude factor
                    realized = direction * float(fwd[t]) * 100.0
                    # subtract fixed costs once per "trade" event
                    pnl = realized - self.cost.total_pct()
                    # only count as trade if we took a side
                else:
                    pnl = -self.cost.total_pct()  # no label → cost of attempted trade, conservative

            test_pnls.append(pnl)
            test_regimes.append(regime)
            test_signals.append(sig)

        bars_per_day = 1.0  # unknown; use sample count as proxy days
        sc = build_scorecard(
            test_pnls,
            regimes=test_regimes,
            observation_days=float(len(test_pnls)),
        )
        ci = block_bootstrap_mean_ci(test_pnls, block_size=max(5, len(test_pnls) // 20), n_boot=100)
        return FoldResult(
            fold=split.fold,
            split=split,
            pnls=test_pnls,
            regimes=test_regimes,
            signals=test_signals,
            n_test=len(test_pnls),
            scorecard=sc,
            bootstrap_ci=ci,
        )

    def run(
        self,
        ds: PITDataset,
        train_size: int = 200,
        valid_size: int = 50,
        test_size: int = 50,
        purge_size: int = 5,
        step: Optional[int] = None,
    ) -> WalkForwardReport:
        ds.assert_no_lookahead_labels_in_features()
        splits = generate_walk_forward_splits(
            ds.n, train_size, valid_size, test_size, purge_size, step=step
        )
        folds: List[FoldResult] = []
        all_pnls: List[float] = []
        all_regimes: List[str] = []
        for sp in splits:
            fr = self.evaluate_fold(ds, sp)
            folds.append(fr)
            all_pnls.extend(fr.pnls)
            all_regimes.extend(fr.regimes)

        agg = build_scorecard(all_pnls, regimes=all_regimes, observation_days=float(len(all_pnls)))
        agg_ci = block_bootstrap_mean_ci(
            all_pnls, block_size=max(5, len(all_pnls) // 20 or 5), n_boot=150
        )
        notes = [
            "cost_adjusted=True (heuristic costs; NOT calibrated expected return)",
            "signals from StrategyEngine confluence only",
            "no RiskEngine/Execution path in this runner",
            f"entry_threshold={self.entry_threshold}",
        ]
        if not folds:
            notes.append("INSUFFICIENT_SAMPLES_FOR_SPLITS")
        return WalkForwardReport(
            symbol=ds.symbol,
            feature_schema_hash=ds.feature_schema_hash,
            folds=folds,
            aggregate=agg,
            aggregate_bootstrap_ci=agg_ci,
            cost_model=self.cost.as_dict(),
            notes=notes,
        )


def compare_champion_challenger(
    champion_report: WalkForwardReport,
    challenger_report: WalkForwardReport,
) -> Dict[str, Scorecard]:
    """Extract scorecards for PromotionGate.evaluate."""
    return {
        "champion": champion_report.aggregate,
        "challenger": challenger_report.aggregate,
    }
