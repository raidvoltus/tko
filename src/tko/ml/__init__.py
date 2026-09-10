"""ML data foundation, backtest harness, and optional signal filter (Stage 5).

Stage 5.1 adds diversity, model pool, ensemble, and computational governor.
ML is additive — never replaces risk/execution gates. LIVE orders still require
RiskEngine approval and LifecycleGovernor authorization. Default OFF.
"""

from __future__ import annotations

from tko.ml.backtest import BacktestMetrics, BacktestResult, run_rule_baseline
from tko.ml.ensemble import NnlsEnsemble, SoftVotingEnsemble
from tko.ml.features import FeatureRow, build_feature_matrix
from tko.ml.filter import FilterDecision, MlSignalFilter
from tko.ml.governor import (
    ComputationalGovernor,
    GovernorConfig,
    GovernorLevel,
    ResourceSnapshot,
)
from tko.ml.labels import LabelConfig, label_forward_direction
from tko.ml.ohlcv_store import OhlcvStore
from tko.ml.pool import ModelPool
from tko.ml.walk_forward import WalkForwardResult, walk_forward_classify

__all__ = [
    "BacktestMetrics",
    "BacktestResult",
    "ComputationalGovernor",
    "FeatureRow",
    "FilterDecision",
    "GovernorConfig",
    "GovernorLevel",
    "LabelConfig",
    "MlSignalFilter",
    "ModelPool",
    "NnlsEnsemble",
    "OhlcvStore",
    "ResourceSnapshot",
    "SoftVotingEnsemble",
    "WalkForwardResult",
    "build_feature_matrix",
    "label_forward_direction",
    "run_rule_baseline",
    "walk_forward_classify",
]
