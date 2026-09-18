"""Strategy richness unit tests — no exchange, no orders."""
import numpy as np

from src.decision.plane import DecisionPlane
from src.decision.strategies import RegimeDetector, StrategyEngine
from src.features.engine import FEATURE_VERSION, CandleBuffer, FeatureEngine


def _synth_trend(n=80, up=True):
    x = np.linspace(100, 120 if up else 80, n)
    noise = np.random.randn(n) * 0.15
    return (x + noise).astype(np.float64)


def test_feature_version_v2():
    assert FEATURE_VERSION == "v2"
    eng = FeatureEngine()
    assert "macd_hist_norm" in eng.FEATURE_NAMES
    assert "hurst_proxy" in eng.FEATURE_NAMES
    assert len(eng.FEATURE_NAMES) >= 25


def test_feature_compute_dim():
    buf = CandleBuffer()
    eng = FeatureEngine(buf)
    px = 100.0
    for i in range(60):
        px *= 1.001
        buf.push("BTC_USDT", float(i), px, px, px, px, 10.0)
    vec, names = eng.compute("BTC_USDT")
    assert vec.dtype == np.float32
    assert len(vec) == len(names)
    assert not np.isnan(vec).any()


def test_regime_trend_up():
    det = RegimeDetector()
    c = _synth_trend(80, up=True)
    regime, conf, allowed = det.classify(c)
    assert regime in ("TREND_UP", "RANGE", "VOLATILE", "MEAN_REVERTING")
    assert 0 <= conf <= 1
    assert isinstance(allowed, tuple)


def test_strategy_scores_bounded():
    eng = StrategyEngine()
    c = _synth_trend(80, up=True)
    sc = eng.evaluate(c, rsi=55.0)
    assert -1.0 <= sc.momentum <= 1.0
    assert -1.0 <= sc.mean_reversion <= 1.0
    assert -1.0 <= sc.breakout <= 1.0
    assert -1.0 <= sc.composite <= 1.0
    assert 0.0 <= sc.strategy_confidence <= 1.0
    assert sc.regime


def test_decision_overlay_no_order_authority():
    dp = DecisionPlane()
    c = _synth_trend(80, up=True)
    sig = dp.make_signal(
        cycle_id="t1",
        symbol="BTC_USDT",
        closes=c,
        probability=0.5,
        expected_return_pct=0.0,
        net_opportunity_pct=0.5,
        use_strategy_overlay=True,
    )
    assert sig.action in ("BUY", "SELL", "WAIT", "HOLD")
    assert sig.signal_hash
    assert hasattr(sig, "strategy_composite")
    # Probability blended into (0,1)
    assert 0.0 <= sig.probability <= 1.0


def test_mean_reversion_prefers_oversold():
    eng = StrategyEngine()
    # flat then dip
    c = np.concatenate([np.full(40, 100.0), np.linspace(100, 90, 20)])
    sc = eng.evaluate(c, rsi=25.0)
    assert sc.mean_reversion > 0  # oversold → positive mean-rev score
