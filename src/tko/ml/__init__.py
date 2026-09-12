"""ML foundation + autonomous training (Stage ML).

ML is a secondary meta-label filter only. Stage 5.1 RiskEngine remains
the sole BUY authorization authority. Default: ML OFF.
"""

from __future__ import annotations

from tko.ml.artifacts import load_and_verify_bundle, publish_model_bundle
from tko.ml.backtest import BacktestMetrics, BacktestResult, run_rule_baseline
from tko.ml.calibration import PlattCalibrator, brier_score, evaluate_calibration
from tko.ml.cusum import CusumConfig, CusumEvent, symmetric_cusum_events
from tko.ml.data_quality import DataQualityReport, validate_ohlcv
from tko.ml.features import FeatureRow, build_feature_matrix
from tko.ml.filter import FilterDecision, MlSignalFilter
from tko.ml.labels import LabelConfig, label_forward_direction
from tko.ml.metrics_gates import deflated_sharpe_ratio, evaluate_deployment_gates
from tko.ml.ohlcv_store import OhlcvStore
from tko.ml.purged_cv import Fold, combinatorial_purged_cv, purged_kfold
from tko.ml.registry import ModelRecord, ModelRegistry, ModelState
from tko.ml.retrain import RetrainPolicy, RetrainState, run_retrain_cycle
from tko.ml.rollback import rollback_to_baseline
from tko.ml.shadow import ShadowLogger, shadow_decision
from tko.ml.tbm import TbmConfig, TbmLabel, meta_label_from_tbm, triple_barrier_labels
from tko.ml.train_pipeline import TrainConfig, TrainReport, run_training
from tko.ml.walk_forward import WalkForwardResult, walk_forward_classify

__all__ = [
    "BacktestMetrics",
    "BacktestResult",
    "CusumConfig",
    "CusumEvent",
    "DataQualityReport",
    "FeatureRow",
    "FilterDecision",
    "Fold",
    "LabelConfig",
    "MlSignalFilter",
    "ModelRecord",
    "ModelRegistry",
    "ModelState",
    "OhlcvStore",
    "PlattCalibrator",
    "RetrainPolicy",
    "RetrainState",
    "ShadowLogger",
    "TbmConfig",
    "TbmLabel",
    "TrainConfig",
    "TrainReport",
    "WalkForwardResult",
    "brier_score",
    "build_feature_matrix",
    "combinatorial_purged_cv",
    "deflated_sharpe_ratio",
    "evaluate_calibration",
    "evaluate_deployment_gates",
    "label_forward_direction",
    "load_and_verify_bundle",
    "meta_label_from_tbm",
    "publish_model_bundle",
    "purged_kfold",
    "rollback_to_baseline",
    "run_retrain_cycle",
    "run_rule_baseline",
    "run_training",
    "shadow_decision",
    "symmetric_cusum_events",
    "triple_barrier_labels",
    "validate_ohlcv",
    "walk_forward_classify",
]
