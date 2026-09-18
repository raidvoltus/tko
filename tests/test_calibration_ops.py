"""Hardened calibration: leakage, artifact integrity, diagnostics, no order authority."""
from pathlib import Path

import numpy as np

from src.evaluation.calibration import (
    BetaCalibrator,
    CalibrationArtifact,
    CalibratorSelector,
    EdgeCalibrator,
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    TemperatureCalibrator,
    calibration_slope_intercept_linear,
    calibration_slope_intercept_logit,
    paired_block_bootstrap_delta,
    verify_artifact,
)
from src.evaluation.pit_dataset import build_pit_from_closes
from src.evaluation.runner import WalkForwardRunner
from src.evaluation.walk_forward import generate_walk_forward_splits


def test_logit_and_linear_slope():
    rng = np.random.default_rng(0)
    y = (rng.random(200) > 0.4).astype(float)
    p = np.clip(0.3 + 0.4 * y + rng.normal(0, 0.05, 200), 0.01, 0.99)
    a, b = calibration_slope_intercept_logit(y, p)
    assert np.isfinite(a) and np.isfinite(b)
    il, sl = calibration_slope_intercept_linear(y, p)
    assert np.isfinite(il) and np.isfinite(sl)


def test_transform_finite_bounded():
    for Cal in (IdentityCalibrator, PlattCalibrator, IsotonicCalibrator, BetaCalibrator, TemperatureCalibrator):
        cal = Cal()
        rng = np.random.default_rng(1)
        y = (rng.random(120) > 0.5).astype(float)
        s = np.clip(rng.random(120), 0.01, 0.99)
        cal.fit(s, y)
        out = cal.transform([0.0, 0.5, 1.0, -5.0, 5.0])
        assert np.all(np.isfinite(out))
        assert np.all(out >= 0) and np.all(out <= 1)


def test_one_class_invalid():
    s = np.linspace(0.1, 0.9, 50)
    y = np.ones(50)
    cal = PlattCalibrator().fit(s, y)
    assert cal.status == "INVALID" or not cal.validate()


def test_selector_identity_baseline():
    rng = np.random.default_rng(2)
    y = (rng.random(200) > 0.5).astype(float)
    s = np.clip(y * 0.5 + 0.25 + rng.normal(0, 0.2, 200), 0.01, 0.99)
    cal, art = CalibratorSelector(min_n=30).select(s[:100], y[:100], s[100:], y[100:])
    assert art.calibration_status in ("CALIBRATED", "UNCALIBRATED")
    assert art.identity_hash
    h1 = art.identity_hash
    art.fitted_at = 999999.0
    assert art.compute_identity_hash() == h1  # timestamp not in identity


def test_artifact_hash_changes_with_dataset():
    art = CalibrationArtifact(calibrator_type="platt", dataset_hash="a", feature_schema_hash="f").seal()
    art2 = CalibrationArtifact(calibrator_type="platt", dataset_hash="b", feature_schema_hash="f").seal()
    assert art.identity_hash != art2.identity_hash


def test_verify_artifact_mismatch():
    art = CalibrationArtifact(feature_schema_hash="X", model_identity_hash="M").seal()
    ok, reason = verify_artifact(art, expected_feature_schema_hash="Y")
    assert not ok and reason == "FEATURE_SCHEMA_MISMATCH"
    ok2, _ = verify_artifact(art, expected_feature_schema_hash="X")
    assert ok2


def test_corruption_fails():
    art = CalibrationArtifact(calibrator_type="beta", n_fit=10).seal()
    art.calibrator_type = "hacked"
    ok, reason = verify_artifact(art)
    assert not ok and reason == "HASH_MISMATCH"


def test_paired_bootstrap_seed():
    y = np.array([1.0, 0, 1, 0, 1] * 20)
    p1 = np.clip(y * 0.8 + 0.1, 0, 1)
    p0 = np.full_like(y, 0.5)
    a = paired_block_bootstrap_delta(y, p1, p0, seed=1, n_boot=50)
    b = paired_block_bootstrap_delta(y, p1, p0, seed=1, n_boot=50)
    assert a["delta_brier"] == b["delta_brier"]
    assert a["ci_low"] == b["ci_low"]


def test_edge_status_explicit():
    e = EdgeCalibrator()
    val, st = e.expected_net_edge_pct(0.5)
    assert st == "UNCALIBRATED_HEURISTIC"


def test_walkforward_segments_ordering():
    splits = generate_walk_forward_splits(500, 150, 40, 40, 5, step=40)
    assert len(splits) >= 1
    for sp in splits:
        sp.validate()
        assert sp.train_end <= sp.purge_end <= sp.cal_fit_end <= sp.cal_select_end <= sp.test_end


def test_wf_no_oos_in_selection_metadata():
    rng = np.random.default_rng(5)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, 500)))
    ds = build_pit_from_closes("BTC_USDT", closes)
    rep = WalkForwardRunner(entry_threshold=0.05).run(
        ds, train_size=150, valid_size=40, test_size=40, purge_size=5, step=40
    )
    assert "TOKOCRYPTO_REAL_OOS_EVIDENCE=PENDING" in rep.notes[0] or any(
        "TOKOCRYPTO" in n for n in rep.notes
    )
    for fr in rep.folds:
        meta = fr.split.to_meta()
        assert meta["cal_select_end"] <= meta["test_end"]


def test_no_order_authority_in_calibration_modules():
    root = Path("src/evaluation")
    for p in root.glob("*.py"):
        text = p.read_text()
        assert "new_order" not in text
        assert "RestClient" not in text
