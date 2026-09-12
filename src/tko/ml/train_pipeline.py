"""Autonomous ML training pipeline — meta-labeling only, no live execution.

Stage 5.1 core remains sovereign. This module never places orders.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tko.core.types import OHLCV
from tko.ml.artifacts import publish_model_bundle
from tko.ml.calibration import PlattCalibrator, evaluate_calibration
from tko.ml.cusum import CusumConfig, symmetric_cusum_events
from tko.ml.data_quality import validate_ohlcv
from tko.ml.features import build_feature_matrix
from tko.ml.metrics_gates import (
    deflated_sharpe_ratio,
    evaluate_deployment_gates,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
)
from tko.ml.models import make_classifier, sklearn_available
from tko.ml.purged_cv import combinatorial_purged_cv, purged_kfold
from tko.ml.regime import regime_not_extreme_degraded, regime_performance, tag_regimes
from tko.ml.registry import ModelRegistry, ModelState
from tko.ml.tbm import TbmConfig, meta_label_from_tbm, triple_barrier_labels

logger = logging.getLogger(__name__)

FEATURE_VERSION = "v1_causal_ohlcv"


@dataclass
class TrainConfig:
    timeframe: str = "15m"
    cusum_threshold: float = 0.015
    tbm_pt: float = 1.0
    tbm_sl: float = 1.0
    tbm_horizon: int = 12
    n_splits: int = 5
    embargo: int = 2
    label_horizon: int = 12
    model_kinds: tuple[str, ...] = ("logreg", "rf")
    min_events: int = 30
    random_seed: int = 42
    n_trials: int = 1  # updated by search


@dataclass
class TrainReport:
    run_id: str
    decision: str  # REJECTED | SHADOW_ELIGIBLE
    data_quality: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    gates: dict[str, Any] = field(default_factory=dict)
    model_id: str = ""
    artifact_dir: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "decision": self.decision,
            "data_quality": self.data_quality,
            "metrics": self.metrics,
            "gates": self.gates,
            "model_id": self.model_id,
            "artifact_dir": self.artifact_dir,
            "notes": list(self.notes),
        }


def _dataset_fingerprint(candles: Sequence[OHLCV]) -> str:
    h = hashlib.sha256()
    for c in candles:
        h.update(str(int(c.timestamp_ms)).encode())
        h.update(f"{c.close:.8f}".encode())
    return h.hexdigest()[:16]


def _feature_vector(row) -> list[float]:
    return [
        float(row.ret_1),
        float(row.ret_4),
        float(row.volatility_8),
        float(row.rsi_14),
        float(row.ema_fast),
        float(row.ema_slow),
        float(row.ema_spread),
        float(row.volume_z),
        float(row.hour_utc),
        float(row.dow_utc),
    ]


FEATURE_SCHEMA = {
    "version": FEATURE_VERSION,
    "columns": [
        "ret_1",
        "ret_4",
        "volatility_8",
        "rsi_14",
        "ema_fast",
        "ema_slow",
        "ema_spread",
        "volume_z",
        "hour_utc",
        "dow_utc",
    ],
}


def run_training(
    candles: list[OHLCV],
    *,
    registry_dir: Path,
    artifact_root: Path,
    config: TrainConfig | None = None,
    baseline_pnls: list[float] | None = None,
    baseline_equity: list[float] | None = None,
) -> TrainReport:
    """Full training cycle. Never touches exchange/execution.

    Returns REJECTED or SHADOW_ELIGIBLE. Does not activate ML in production.
    """
    cfg = config or TrainConfig()
    run_id = uuid.uuid4().hex[:12]
    report = TrainReport(run_id=run_id, decision="REJECTED")

    if not sklearn_available():
        report.notes.append("sklearn_unavailable")
        return report

    clean, dq = validate_ohlcv(candles, timeframe=cfg.timeframe)
    report.data_quality = dq.to_dict()
    if dq.usable_rows < cfg.min_events * 3:
        report.notes.append("insufficient_clean_rows")
        return report

    closes = [float(c.close) for c in clean]
    ts = [int(c.timestamp_ms) for c in clean]
    events = symmetric_cusum_events(
        closes, ts, config=CusumConfig(threshold=cfg.cusum_threshold)
    )
    # Meta-label only BUY-side primary events (up CUSUM as proxy for long interest)
    buy_events = [e for e in events if e.side == 1]
    if len(buy_events) < cfg.min_events:
        report.notes.append(f"insufficient_buy_events:{len(buy_events)}")
        return report

    tbm = triple_barrier_labels(
        closes,
        [e.index for e in buy_events],
        config=TbmConfig(
            pt_mult=cfg.tbm_pt,
            sl_mult=cfg.tbm_sl,
            max_horizon=cfg.tbm_horizon,
        ),
    )
    meta = meta_label_from_tbm(tbm)
    rows = build_feature_matrix(clean)
    # map event index → feature row index (features shorter due to warmup)
    # FeatureRow has no index field in current schema — align by position offset
    feat_offset = len(clean) - len(rows)
    X: list[list[float]] = []
    y: list[int] = []
    event_idx_for_sample: list[int] = []
    meta_map = {ei: lab for ei, lab in meta}
    for e in buy_events:
        fi = e.index - feat_offset
        if fi < 0 or fi >= len(rows):
            continue
        if e.index not in meta_map:
            continue
        X.append(_feature_vector(rows[fi]))
        y.append(int(meta_map[e.index]))
        event_idx_for_sample.append(e.index)

    if len(X) < cfg.min_events:
        report.notes.append(f"insufficient_labeled_samples:{len(X)}")
        return report

    # Purged CV + CPCV accuracy distribution
    folds = purged_kfold(
        len(X), n_splits=cfg.n_splits, embargo=cfg.embargo, label_horizon=cfg.label_horizon
    )
    cpcv = combinatorial_purged_cv(
        len(X), n_groups=min(6, max(3, len(X) // 10)), n_test_groups=2,
        embargo=cfg.embargo, label_horizon=cfg.label_horizon,
    )
    cfg.n_trials = max(1, len(cfg.model_kinds) * max(1, len(cpcv.folds)))

    best_kind = cfg.model_kinds[0]
    best_oos_probs: list[float] = []
    best_oos_y: list[int] = []
    fold_accs: list[float] = []

    for kind in cfg.model_kinds:
        accs = []
        oos_p: list[float] = []
        oos_y: list[int] = []
        for fold in folds:
            tr_x = [X[i] for i in fold.train_idx]
            tr_y = [y[i] for i in fold.train_idx]
            te_x = [X[i] for i in fold.test_idx]
            te_y = [y[i] for i in fold.test_idx]
            if len(set(tr_y)) < 2 or not te_x:
                continue
            clf = make_classifier(kind)
            try:
                clf.fit(tr_x, tr_y)
            except Exception:
                logger.exception("classifier fit failed for fold; skipping")
                continue
            if hasattr(clf, "predict_proba"):
                proba = clf.predict_proba(te_x)
                # class 1 probability
                classes = list(getattr(clf, "classes_", [0, 1]))
                if 1 in classes:
                    ci = classes.index(1)
                    probs = [float(p[ci]) for p in proba]
                else:
                    probs = [float(max(p)) for p in proba]
            else:
                pred = list(clf.predict(te_x))
                probs = [1.0 if int(p) == 1 else 0.0 for p in pred]
            pred_lbl = [1 if p >= 0.5 else 0 for p in probs]
            acc = sum(1 for a, b in zip(pred_lbl, te_y) if a == b) / len(te_y)
            accs.append(acc)
            oos_p.extend(probs)
            oos_y.extend(te_y)
        mean_acc = sum(accs) / len(accs) if accs else 0.0
        fold_accs.append(mean_acc)
        if len(oos_p) > len(best_oos_probs):
            best_kind = kind
            best_oos_probs = oos_p
            best_oos_y = oos_y

    if not best_oos_probs:
        report.notes.append("no_oos_predictions")
        return report

    calibrator = PlattCalibrator()
    calibrator.fit(best_oos_probs, best_oos_y)
    calibrated = calibrator.transform(best_oos_probs)
    cal_report = evaluate_calibration(best_oos_y, calibrated, method="platt")

    # Synthetic trade pnls from meta labels for gate metrics (OOS)
    ml_pnls = [0.01 if (p >= 0.55 and yt == 1) else (-0.01 if p >= 0.55 else 0.0)
               for p, yt in zip(calibrated, best_oos_y)]
    base_pnls = baseline_pnls if baseline_pnls is not None else [0.005 if yt == 1 else -0.005 for yt in best_oos_y]
    # align lengths
    m = min(len(ml_pnls), len(base_pnls))
    ml_pnls, base_pnls = ml_pnls[:m], base_pnls[:m]
    ml_eq = [100.0]
    base_eq = [100.0]
    for p in ml_pnls:
        ml_eq.append(ml_eq[-1] * (1.0 + p))
    for p in base_pnls:
        base_eq.append(base_eq[-1] * (1.0 + p))

    ml_sharpe = sharpe_ratio(ml_pnls)
    base_sharpe = sharpe_ratio(base_pnls)
    ml_mdd = max_drawdown(ml_eq)
    base_mdd = max_drawdown(base_eq)
    ml_pf = profit_factor(ml_pnls)
    base_pf = profit_factor(base_pnls)
    ml_trades = sum(1 for p in ml_pnls if abs(p) > 1e-12)
    base_trades = sum(1 for p in base_pnls if abs(p) > 1e-12)
    dsr = deflated_sharpe_ratio(ml_sharpe, n_obs=len(ml_pnls), n_trials=cfg.n_trials)

    tags = tag_regimes(closes)
    # map samples to event indices for regime
    sample_indices = event_idx_for_sample[:m] if event_idx_for_sample else list(range(m))
    base_reg = regime_performance(tags, base_pnls, sample_indices)
    ml_reg = regime_performance(tags, ml_pnls, sample_indices)
    regime_ok = regime_not_extreme_degraded(base_reg, ml_reg)

    gate_report = evaluate_deployment_gates(
        baseline_sharpe=base_sharpe,
        ml_sharpe=ml_sharpe,
        baseline_mdd=base_mdd,
        ml_mdd=ml_mdd,
        baseline_pf=base_pf,
        ml_pf=ml_pf,
        baseline_trades=base_trades,
        ml_trades=ml_trades,
        dsr=dsr,
        regime_ok=regime_ok,
        n_trials=cfg.n_trials,
    )
    report.gates = gate_report.to_dict()
    report.metrics = {
        "best_kind": best_kind,
        "fold_accs": fold_accs,
        "cpcv_folds": len(cpcv.folds),
        "brier": cal_report.brier,
        "ece": cal_report.ece,
        "ml_sharpe": ml_sharpe,
        "baseline_sharpe": base_sharpe,
        "ml_mdd": ml_mdd,
        "baseline_mdd": base_mdd,
        "ml_pf": ml_pf,
        "baseline_pf": base_pf,
        "ml_trades": ml_trades,
        "baseline_trades": base_trades,
        "dsr": dsr,
        "n_trials": cfg.n_trials,
        "n_samples": len(X),
        "n_buy_events": len(buy_events),
        "dataset_fp": _dataset_fingerprint(clean),
        "feature_version": FEATURE_VERSION,
    }

    # Fit final model on all data for artifact (still not ACTIVE)
    if len(set(y)) < 2:
        report.notes.append("single_class_labels")
        return report
    final_clf = make_classifier(best_kind)
    final_clf.fit(X, y)

    # Serialize via joblib if available else pickle
    model_bytes: bytes
    try:
        import io

        import joblib
        buf = io.BytesIO()
        joblib.dump({"model": final_clf, "calibrator": calibrator, "kind": best_kind}, buf)
        model_bytes = buf.getvalue()
    except Exception:  # noqa: BLE001
        import pickle
        model_bytes = pickle.dumps({"model": final_clf, "calibrator": calibrator, "kind": best_kind})

    artifact_dir = artifact_root / run_id
    metadata = {
        "run_id": run_id,
        "feature_version": FEATURE_VERSION,
        "cusum_threshold": cfg.cusum_threshold,
        "tbm": {"pt": cfg.tbm_pt, "sl": cfg.tbm_sl, "horizon": cfg.tbm_horizon},
        "model_kind": best_kind,
        "random_seed": cfg.random_seed,
        "dataset_fp": report.metrics["dataset_fp"],
        "created_at": time.time(),
    }
    checksums = publish_model_bundle(
        artifact_dir,
        model_bytes=model_bytes,
        metadata=metadata,
        metrics=report.metrics,
        calibration=cal_report.to_dict(),
        feature_schema=FEATURE_SCHEMA,
    )
    report.artifact_dir = str(artifact_dir)
    report.notes.append(f"checksums:{checksums.get('model.joblib', '')[:12]}")

    registry = ModelRegistry(registry_dir / "registry.jsonl")
    rec = registry.register_trained(
        artifact_dir=str(artifact_dir),
        run_id=run_id,
        feature_version=FEATURE_VERSION,
        metrics=report.metrics,
    )
    report.model_id = rec.model_id
    try:
        registry.transition(rec.model_id, ModelState.VALIDATED, notes="purged_cv_done")
        registry.transition(rec.model_id, ModelState.CALIBRATED, notes="platt")
        if gate_report.eligible:
            registry.transition(rec.model_id, ModelState.ELIGIBLE, notes="all_gates_pass")
            registry.transition(rec.model_id, ModelState.SHADOW, notes="shadow_eligible")
            report.decision = "SHADOW_ELIGIBLE"
        else:
            registry.transition(rec.model_id, ModelState.REJECTED, notes="gates_failed")
            report.decision = "REJECTED"
    except ValueError as exc:
        report.notes.append(f"registry_transition:{exc}")
        report.decision = "REJECTED"

    # Never set ACTIVE here — requires separate promotion after shadow evaluation
    return report
