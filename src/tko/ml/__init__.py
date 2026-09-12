"""ML foundation + autonomous training (Stage ML).

ML is a secondary meta-label filter only. Stage 5.1 RiskEngine remains
the sole BUY authorization authority. Default: ML OFF.
"""

from __future__ import annotations

from tko.ml.backtest import BacktestMetrics, BacktestResult, run_rule_baseline
from tko.ml.features import FeatureRow, build_feature_matrix
from tko.ml.filter import MlSignalFilter, FilterDecision
from tko.ml.labels import LabelConfig, label_forward_direction
from tko.ml.ohlcv_store import OhlcvStore
from tko.ml.walk_forward import WalkForwardResult, walk_forward_classify
from tko.ml.data_quality import DataQualityReport, validate_ohlcv
from tko.ml.cusum import CusumConfig, CusumEvent, symmetric_cusum_events
from tko.ml.tbm import TbmConfig, TbmLabel, triple_barrier_labels, meta_label_from_tbm
from tko.ml.purged_cv import Fold, purged_kfold, combinatorial_purged_cv
from tko.ml.calibration import brier_score, evaluate_calibration, PlattCalibrator
from tko.ml.metrics_gates import evaluate_deployment_gates, deflated_sharpe_ratio
from tko.ml.registry import ModelRegistry, ModelState, ModelRecord
from tko.ml.artifacts import publish_model_bundle, load_and_verify_bundle
from tko.ml.train_pipeline import TrainConfig, TrainReport, run_training
from tko.ml.shadow import ShadowLogger, shadow_decision
from tko.ml.retrain import RetrainPolicy, RetrainState, run_retrain_cycle
from tko.ml.rollback import rollback_to_baseline

__all__ = [
    "BacktestMetrics", "BacktestResult", "FeatureRow", "FilterDecision",
    "LabelConfig", "MlSignalFilter", "OhlcvStore", "WalkForwardResult",
    "DataQualityReport", "CusumConfig", "CusumEvent", "TbmConfig", "TbmLabel",
    "Fold", "ModelRegistry", "ModelState", "ModelRecord", "TrainConfig",
    "TrainReport", "RetrainPolicy", "RetrainState", "ShadowLogger",
    "PlattCalibrator", "build_feature_matrix", "label_forward_direction",
    "run_rule_baseline", "walk_forward_classify", "validate_ohlcv",
    "symmetric_cusum_events", "triple_barrier_labels", "meta_label_from_tbm",
    "purged_kfold", "combinatorial_purged_cv", "brier_score",
    "evaluate_calibration", "evaluate_deployment_gates", "deflated_sharpe_ratio",
    "publish_model_bundle", "load_and_verify_bundle", "run_training",
    "shadow_decision", "run_retrain_cycle", "rollback_to_baseline",
]
