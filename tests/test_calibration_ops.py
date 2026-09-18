"""Scientific calibration framework tests."""
import csv
from pathlib import Path

import numpy as np

from src.evaluation.calibration import (
    BetaCalibrator,
    CalibratorSelector,
    EdgeCalibrator,
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    TemperatureCalibrator,
    brier_score,
    expected_calibration_error,
    full_metrics,
    log_loss,
    maximum_calibration_error,
)
from src.evaluation.ohlcv_loader import load_ohlcv_csv
from src.evaluation.pit_dataset import build_pit_from_closes
from src.evaluation.runner import WalkForwardRunner
from src.observability.metrics import CycleMetric, MetricsRegistry


def test_metrics_suite():
    y = [1, 0, 1, 0]
    p = [0.9, 0.1, 0.8, 0.2]
    assert brier_score(y, p) < 0.1
    assert log_loss(y, p) < 1.0
    assert expected_calibration_error(y, p) >= 0
    assert maximum_calibration_error(y, p) >= 0
    m = full_metrics(y, p)
    assert "slope" in m and "intercept" in m


def test_selector_picks_or_identity():
    rng = np.random.default_rng(0)
    y = (rng.random(300) > 0.55).astype(float)
    # systematically overconfident scores
    scores = np.clip(0.5 + 0.45 * (y - 0.5) / 0.5 + rng.normal(0, 0.05, 300), 0.01, 0.99)
    sel = CalibratorSelector(min_brier_improve=0.001, min_n=40)
    cal, art = sel.select(scores[:150], y[:150], scores[150:], y[150:])
    assert art.version
    assert art.selected in ("identity", "platt", "isotonic", "beta", "temperature")
    assert "brier" in art.metrics_selected
    assert isinstance(cal.transform([0.5])[0], float)


def test_beta_fit():
    rng = np.random.default_rng(1)
    s = rng.uniform(0.05, 0.95, 200)
    y = (s + rng.normal(0, 0.15, 200) > 0.5).astype(float)
    cal = BetaCalibrator().fit(s, y)
    assert cal.fitted
    out = cal.transform(s[:5])
    assert out.shape == (5,)
    assert np.all((out >= 0) & (out <= 1))


def test_temperature_fit():
    rng = np.random.default_rng(2)
    s = rng.uniform(0.1, 0.9, 150)
    y = (s > 0.5).astype(float)
    cal = TemperatureCalibrator().fit(s, y)
    assert cal.fitted and cal.T > 0


def test_platt_isotonic_edge():
    rng = np.random.default_rng(3)
    y = (rng.random(400) > 0.6).astype(float)
    scores = np.clip(y * 0.3 + 0.6 + rng.normal(0, 0.05, 400), 0, 1)
    assert PlattCalibrator().fit(scores, y).fitted
    assert IsotonicCalibrator().fit(scores, y).fitted
    comps = np.linspace(-1, 1, 200)
    realized = comps * 3 + rng.normal(0, 0.5, 200)
    assert EdgeCalibrator().fit(comps, realized).fitted


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


def test_wf_report_calibration_artifact_fields():
    rng = np.random.default_rng(4)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, 500)))
    ds = build_pit_from_closes("BTC_USDT", closes)
    rep = WalkForwardRunner(entry_threshold=0.05).run(
        ds, train_size=150, valid_size=40, test_size=40, purge_size=5, step=40
    )
    assert "selected" in rep.calibration or "error" in rep.calibration


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
    assert reg.snapshot()["counters"]["risk_deny"] == 1


def test_identity_no_authority():
    """Calibration modules must not reference order submission."""
    import src.evaluation.calibration as cal_mod
    src = Path(cal_mod.__file__).read_text()
    assert "new_order" not in src
    assert "RestClient" not in src
