"""Stage ML: autonomous training pipeline tests — Stage 5.1 authority untouched."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.core.types import OHLCV, Signal
from tko.ml.artifacts import load_and_verify_bundle, publish_model_bundle
from tko.ml.calibration import brier_score, evaluate_calibration
from tko.ml.cusum import CusumConfig, symmetric_cusum_events
from tko.ml.data_quality import validate_ohlcv
from tko.ml.features import build_feature_matrix
from tko.ml.filter import MlSignalFilter
from tko.ml.metrics_gates import deflated_sharpe_ratio, evaluate_deployment_gates
from tko.ml.purged_cv import combinatorial_purged_cv, purged_kfold
from tko.ml.registry import ModelRegistry, ModelState
from tko.ml.rollback import rollback_to_baseline
from tko.ml.shadow import ShadowLogger, shadow_decision
from tko.ml.tbm import TbmConfig, triple_barrier_labels
from tko.ml.train_pipeline import TrainConfig, run_training
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.strategy.btc import TradeDecision


def _synth(n: int = 400, start_px: float = 100.0) -> list[OHLCV]:
    out: list[OHLCV] = []
    px = start_px
    ts = 1_700_000_000_000
    for i in range(n):
        # stronger moves so CUSUM fires
        px = px * (1.0 + 0.008 * math.sin(i / 5.0) + 0.002 * ((i % 7) - 3))
        out.append(
            OHLCV(
                timestamp_ms=ts + i * 900_000,
                open=px,
                high=px * 1.01,
                low=px * 0.99,
                close=px,
                volume=1000.0 + (i % 10) * 50,
            )
        )
    return out


def test_data_quality_detects_duplicates_and_gaps():
    c = _synth(50)
    # duplicate
    c2 = list(c) + [c[10]]
    _clean, rep = validate_ohlcv(c2, timeframe="15m")
    assert rep.duplicates >= 1
    assert rep.usable_rows == 50
    # gap
    gapped = c[:20] + c[25:]
    _, rep2 = validate_ohlcv(gapped, timeframe="15m")
    assert rep2.missing_intervals >= 1


def test_features_causal_no_future_length():
    candles = _synth(100)
    rows = build_feature_matrix(candles)
    assert len(rows) < len(candles)
    assert len(rows) > 10


def test_cusum_deterministic():
    c = _synth(200)
    closes = [x.close for x in c]
    ts = [x.timestamp_ms for x in c]
    a = symmetric_cusum_events(closes, ts, config=CusumConfig(threshold=0.02))
    b = symmetric_cusum_events(closes, ts, config=CusumConfig(threshold=0.02))
    assert [(e.index, e.side) for e in a] == [(e.index, e.side) for e in b]


def test_tbm_labels_and_horizons():
    c = _synth(150)
    closes = [x.close for x in c]
    events = list(range(30, 100, 10))
    labels = triple_barrier_labels(closes, events, config=TbmConfig(max_horizon=8))
    assert len(labels) == len(events)
    assert all(lb.horizon_used >= 0 for lb in labels)
    assert all(lb.label in (-1, 0, 1) for lb in labels)


def test_purged_kfold_and_cpcv():
    folds = purged_kfold(100, n_splits=5, embargo=2, label_horizon=5)
    assert len(folds) >= 3
    for f in folds:
        assert not set(f.train_idx) & set(f.test_idx)
    cpcv = combinatorial_purged_cv(60, n_groups=5, n_test_groups=2, embargo=1, label_horizon=3)
    assert len(cpcv.folds) >= 5


def test_brier_and_calibration():
    y = [1, 0, 1, 1, 0, 0, 1, 0]
    p = [0.9, 0.1, 0.8, 0.7, 0.2, 0.3, 0.6, 0.4]
    b = brier_score(y, p)
    assert 0.0 <= b <= 1.0
    rep = evaluate_calibration(y, p)
    assert rep.n == 8


def test_dsr_and_gates_reject_weak_model():
    dsr = deflated_sharpe_ratio(0.1, n_obs=50, n_trials=20)
    assert 0.0 <= dsr <= 1.0
    report = evaluate_deployment_gates(
        baseline_sharpe=1.0,
        ml_sharpe=0.5,
        baseline_mdd=0.1,
        ml_mdd=0.2,
        baseline_pf=1.5,
        ml_pf=1.0,
        baseline_trades=100,
        ml_trades=10,
        dsr=0.1,
        regime_ok=False,
        n_trials=20,
    )
    assert report.decision == "REJECTED"
    assert report.eligible is False


def test_artifact_checksum_fail_closed(tmp_path: Path):
    d = tmp_path / "bundle"
    publish_model_bundle(
        d,
        model_bytes=b"modeldata",
        metadata={"v": 1},
        metrics={"x": 1},
        calibration={"brier": 0.1},
        feature_schema={"columns": ["a"]},
    )
    ok, reason = load_and_verify_bundle(d)
    assert ok and reason == "ok"
    # corrupt
    (d / "model.joblib").write_bytes(b"tampered")
    ok2, reason2 = load_and_verify_bundle(d)
    assert not ok2
    assert "checksum_mismatch" in reason2


def test_registry_state_machine(tmp_path: Path):
    reg = ModelRegistry(tmp_path / "reg.jsonl")
    rec = reg.register_trained(artifact_dir=str(tmp_path), run_id="r1", feature_version="v1")
    reg.transition(rec.model_id, ModelState.VALIDATED)
    reg.transition(rec.model_id, ModelState.CALIBRATED)
    reg.transition(rec.model_id, ModelState.ELIGIBLE)
    reg.transition(rec.model_id, ModelState.SHADOW)
    with pytest.raises(ValueError):
        reg.transition(rec.model_id, ModelState.TRAINED)  # illegal


def test_shadow_logger(tmp_path: Path):
    log = ShadowLogger(tmp_path / "shadow.jsonl")
    d = shadow_decision(
        primary_signal="BUY", confidence=0.4, min_confidence=0.55,
        model_version="m1", logger=log,
    )
    assert d["would_filter"] is True
    d2 = shadow_decision(
        primary_signal="SELL", confidence=0.1, min_confidence=0.55,
        model_version="m1", logger=log,
    )
    assert d2["would_filter"] is False  # SELL not filtered
    assert len(log.read_all()) == 2


def test_ml_filter_default_off_and_cannot_authorize(tmp_path: Path):
    f = MlSignalFilter(enabled=False)
    assert f.filter(TradeDecision(Signal.BUY, "x", 0.9, 100.0), _synth(60)).allow is True
    # enabled without model blocks BUY
    f2 = MlSignalFilter(enabled=True, model=None)
    assert f2.filter(TradeDecision(Signal.BUY, "x", 0.9, 100.0), _synth(60)).allow is False
    # SELL still allowed
    assert f2.filter(TradeDecision(Signal.SELL, "x", 0.9, 100.0), _synth(60)).allow is True
    # RiskEngine still sovereign
    risk = RiskEngine(
        Settings(min_quote_balance=1, market_data_max_age_sec=0, max_open_positions=1),
        tmp_path,
        DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"),
    )
    # even with high ML confidence narrative, risk rejects when positions full
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000,
        signal=Signal.BUY, open_positions=1,
    )
    assert not dec.approved


def test_ml_cannot_bypass_evaluate_buy(tmp_path: Path):
    risk = RiskEngine(
        Settings(min_quote_balance=1, market_data_max_age_sec=0),
        tmp_path,
        DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"),
    )
    with pytest.raises(RuntimeError, match="not a production authorization path"):
        risk.evaluate_buy(free_quote=1e6, last_price=1000, open_positions=0)


def test_rollback_clears_active(tmp_path: Path):
    reg = ModelRegistry(tmp_path / "reg.jsonl")
    rec = reg.register_trained(artifact_dir=str(tmp_path), run_id="r", feature_version="v1")
    for st in (ModelState.VALIDATED, ModelState.CALIBRATED, ModelState.ELIGIBLE, ModelState.SHADOW, ModelState.ACTIVE):
        reg.transition(rec.model_id, st)
    reg.set_active(rec.model_id)
    assert reg.get_active() is not None
    out = rollback_to_baseline(reg, reason="test")
    assert out["cleared"] is True
    assert reg.get_active() is None


def test_run_training_smoke(tmp_path: Path):
    pytest.importorskip("sklearn")
    candles = _synth(500)
    report = run_training(
        candles,
        registry_dir=tmp_path / "reg",
        artifact_root=tmp_path / "art",
        config=TrainConfig(min_events=10, cusum_threshold=0.01, n_splits=3),
    )
    assert report.run_id
    assert report.decision in ("REJECTED", "SHADOW_ELIGIBLE")
    assert "dataset_fp" in report.metrics or report.notes


def test_settings_ml_default_off():
    s = Settings(min_quote_balance=1)
    assert s.ml_filter_enabled is False
    assert s.ml_governor_enabled is False
