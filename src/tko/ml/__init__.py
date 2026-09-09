"""ML data foundation, backtest harness, and optional signal filter (Stage 5).

ML is additive — never replaces risk/execution gates. LIVE orders still require
RiskEngine approval and LifecycleGovernor authorization.
"""

from __future__ import annotations

from tko.ml.backtest import BacktestMetrics, BacktestResult, run_rule_baseline
from tko.ml.features import FeatureRow, build_feature_matrix
from tko.ml.filter import MlSignalFilter, FilterDecision
from tko.ml.labels import LabelConfig, label_forward_direction
from tko.ml.ohlcv_store import OhlcvStore
from tko.ml.walk_forward import WalkForwardResult, walk_forward_classify

__all__ = [
    "BacktestMetrics",
    "BacktestResult",
    "FeatureRow",
    "FilterDecision",
    "LabelConfig",
    "MlSignalFilter",
    "OhlcvStore",
    "WalkForwardResult",
    "build_feature_matrix",
    "label_forward_direction",
    "run_rule_baseline",
    "walk_forward_classify",
]
