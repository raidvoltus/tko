"""
Walk-forward evaluation + leakage-safe calibration.

Per fold: TRAIN | PURGE | CAL_FIT | CAL_SELECT | TEST(OOS)
Selection never sees OOS. No order authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from src.champion.promotion import Scorecard
from src.decision.strategies import StrategyEngine
from src.evaluation.calibration import CalibratorSelector, EdgeCalibrator
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
    calibration: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WalkForwardReport:
    symbol: str
    feature_schema_hash: str
    folds: List[FoldResult]
    aggregate: Scorecard
    aggregate_bootstrap_ci: Dict[str, float]
    cost_model: Dict[str, float]
    calibration: Dict[str, Any] = field(default_factory=dict)
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
            "calibration": self.calibration,
            "notes": self.notes,
            "folds": [
                {
                    "fold": f.fold,
                    "n_test": f.n_test,
                    "net_pnl": f.scorecard.net_pnl,
                    "calibration_status": f.calibration.get("calibration_status"),
                    "split": f.split.to_meta(),
                }
                for f in self.folds
            ],
        }


@dataclass
class CostModel:
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

    def _signal_at(self, ds: PITDataset, t: int):
        closes = ds.close[: t + 1]
        volumes = ds.volume[: t + 1]
        rsi = float(ds.features["rsi_14"][t]) if "rsi_14" in ds.features else 50.0
        return self.strategies.evaluate(closes, volumes=volumes, rsi=rsi)

    def _collect_region(self, ds: PITDataset, start: int, end: int):
        scores, ys, regimes, comps, realized = [], [], [], [], []
        for t in range(start, end):
            if t < self.min_bars_history:
                continue
            sc = self._signal_at(ds, t)
            p01 = 0.5 + 0.5 * float(sc.composite)
            fwd = ds.labels.get("fwd_ret_1")
            if fwd is None or t >= len(fwd) or not np.isfinite(fwd[t]):
                continue
            y = 1.0 if float(fwd[t]) > 0 else 0.0
            scores.append(p01)
            ys.append(y)
            regimes.append(sc.regime)
            comps.append(float(sc.composite))
            realized.append(
                float(fwd[t]) * 100.0 * (1.0 if sc.composite >= 0 else -1.0) - self.cost.total_pct()
            )
        return scores, ys, regimes, comps, realized

    def evaluate_fold(self, ds: PITDataset, split: WalkForwardSplit) -> FoldResult:
        # Leakage-safe calibration per fold
        s_fit, y_fit, _, _, _ = self._collect_region(ds, split.purge_end, split.cal_fit_end)
        s_sel, y_sel, _, _, _ = self._collect_region(ds, split.cal_fit_end, split.cal_select_end)
        s_oos, y_oos, _, comps_oos, realized_oos = self._collect_region(
            ds, split.cal_select_end, split.test_end
        )
        selector = CalibratorSelector()
        cal, art = selector.select(
            s_fit,
            y_fit,
            s_sel,
            y_sel,
            scores_oos=s_oos,
            y_oos=y_oos,
            feature_schema_hash=ds.feature_schema_hash,
            dataset_hash=getattr(ds, "feature_schema_hash", ""),
            split_meta=split.to_meta(),
        )
        edge = EdgeCalibrator().fit(comps_oos[: max(1, len(comps_oos) // 2)] if comps_oos else [],
                                    realized_oos[: max(1, len(realized_oos) // 2)] if realized_oos else [])
        # note: edge fit on OOS half is only for harness demo — status remains explicit
        # Prefer not claiming CALIBRATED_OOS unless dedicated edge OOS exists; mark heuristic if small
        if edge.n_fit < 50:
            edge.status = "UNCALIBRATED_HEURISTIC"
            edge.fitted = False

        test_pnls: List[float] = []
        test_regimes: List[str] = []
        test_signals: List[str] = []
        for t in range(split.cal_select_end, split.test_end):
            if t < self.min_bars_history:
                continue
            sc = self._signal_at(ds, t)
            regime = sc.regime
            comp = sc.composite
            if abs(comp) < self.entry_threshold or regime in ("UNKNOWN", "VOLATILE"):
                sig, pnl = "FLAT", 0.0
            else:
                sig = "LONG" if comp > 0 else "SHORT"
                fwd = ds.labels.get("fwd_ret_1")
                if fwd is not None and t < len(fwd) and np.isfinite(fwd[t]):
                    direction = 1.0 if sig == "LONG" else -1.0
                    pnl = direction * float(fwd[t]) * 100.0 - self.cost.total_pct()
                else:
                    pnl = -self.cost.total_pct()
            test_pnls.append(pnl)
            test_regimes.append(regime)
            test_signals.append(sig)

        scard = build_scorecard(test_pnls, regimes=test_regimes, observation_days=float(len(test_pnls)))
        ci = block_bootstrap_mean_ci(test_pnls, block_size=max(5, len(test_pnls) // 20 or 5), n_boot=100)
        cal_info = art.to_dict()
        cal_info["edge_status"] = edge.status
        return FoldResult(
            fold=split.fold,
            split=split,
            pnls=test_pnls,
            regimes=test_regimes,
            signals=test_signals,
            n_test=len(test_pnls),
            scorecard=scard,
            bootstrap_ci=ci,
            calibration=cal_info,
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
        statuses = [f.calibration.get("calibration_status") for f in folds]
        notes = [
            "TOKOCRYPTO_REAL_OOS_EVIDENCE=PENDING",
            "fold layout: TRAIN|PURGE|CAL_FIT|CAL_SELECT|TEST",
            "OOS never used for calibrator selection",
            "calibration≠profitability",
            f"folds_calibrated={sum(1 for s in statuses if s == 'CALIBRATED')}/{len(statuses)}",
            "no RiskEngine/Execution path in this runner",
        ]
        if not folds:
            notes.append("INSUFFICIENT_SAMPLES_FOR_SPLITS")
        # aggregate calibration summary
        cal_summary = {
            "n_folds": len(folds),
            "statuses": statuses,
            "TOKOCRYPTO_REAL_OOS_EVIDENCE": "PENDING",
        }
        return WalkForwardReport(
            symbol=ds.symbol,
            feature_schema_hash=ds.feature_schema_hash,
            folds=folds,
            aggregate=agg,
            aggregate_bootstrap_ci=agg_ci,
            cost_model=self.cost.as_dict(),
            calibration=cal_summary,
            notes=notes,
        )


def compare_champion_challenger(
    champion_report: WalkForwardReport,
    challenger_report: WalkForwardReport,
) -> Dict[str, Scorecard]:
    return {
        "champion": champion_report.aggregate,
        "challenger": challenger_report.aggregate,
    }
