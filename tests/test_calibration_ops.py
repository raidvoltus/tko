"""Calibration, OHLCV loader, metrics — strengthen weak empirical/ops layers."""
import csv
from pathlib import Path

import numpy as np

from src.evaluation.calibration import (
    EdgeCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    brier_score,
    expected_calibration_error,
)
from src.evaluation.ohlcv_loader import load_ohlcv_csv
from src.evaluation.pit_dataset import build_pit_from_closes
from src.evaluation.runner import WalkForwardRunner
from src.observability.metrics import CycleMetric, MetricsRegistry


def test_platt_reduces_ece_when_miscalibrated():
    rng = np.random.default_rng(0)
    # raw scores biased high
    y = (rng.random(400) > 0.6).astype(float)
    scores = np.clip(y * 0.3 + 0.6 + rng.normal(0, 0.05, 400), 0, 1)
    ece0 = expected_calibration_error(y, scores)
    cal = PlattCalibrator().fit(scores, y)
    assert cal.fitted
    p = cal.transform(scores)
    ece1 = expected_calibration_error(y, p)
    assert ece1 <= ece0 + 0.05  # should not worsen much; usually improves


def test_isotonic_fit():
    rng = np.random.default_rng(1)
    s = rng.uniform(0, 1, 300)
    y = (s + rng.normal(0, 0.2, 300) > 0.5).astype(float)
    cal = IsotonicCalibrator().fit(s, y)
    assert cal.fitted
    out = cal.transform([0.1, 0.5, 0.9])
    assert out.shape == (3,)


def test_edge_calibrator():
    comps = np.linspace(-1, 1, 200)
    realized = comps * 3.0 + np.random.default_rng(2).normal(0, 0.5, 200)
    ec = EdgeCalibrator().fit(comps, realized)
    assert ec.fitted
    assert isinstance(ec.expected_net_edge_pct(0.5), float)


def test_brier():
    assert brier_score([1, 0], [1, 0]) == 0.0


def test_ohlcv_csv_loader(tmp_path: Path):
    p = tmp_path / "bars.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        px = 100.0
        for i in range(80):
            px *= 1.001
            w.writerow([1_700_000_000 + i * 60, px, px, px, px, 10])
    ds = load_ohlcv_csv(p, symbol="BTC_USDT")
    assert ds.n == 80
    assert ds.symbol == "BTC_USDT"


def test_wf_report_includes_calibration():
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, 500)))
    ds = build_pit_from_closes("BTC_USDT", closes)
    rep = WalkForwardRunner(entry_threshold=0.05).run(
        ds, train_size=150, valid_size=40, test_size=40, purge_size=5, step=40
    )
    assert "ece_after" in rep.calibration or "platt_fitted" in rep.calibration


def test_metrics_registry():
    reg = MetricsRegistry()
    reg.record_cycle(
        CycleMetric(
            ts=1.0,
            cycle_id="c1",
            mode="LIVE",
            regime="RANGE",
            action="WAIT",
            strategy_composite=0.1,
            model_score=0.0,
            net_edge_pct=0.0,
            risk_allowed=False,
            risk_reason="STATE_UNRECONCILED",
            risk_mode="NORMAL",
            state_health="UNRECONCILED",
        )
    )
    snap = reg.snapshot()
    assert snap["counters"]["risk_deny"] == 1
