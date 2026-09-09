"""Stage 5 foundation: OHLCV store, features, labels, backtest, optional ML."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.core.types import OHLCV, Signal
from tko.ml.backtest import run_rule_baseline
from tko.ml.features import build_feature_matrix
from tko.ml.filter import MlSignalFilter
from tko.ml.labels import LabelConfig, label_forward_direction
from tko.ml.ohlcv_store import OhlcvStore
from tko.strategy.btc import TradeDecision


def _synth_candles(n: int = 120, start_px: float = 100.0) -> list[OHLCV]:
    out: list[OHLCV] = []
    px = start_px
    ts = 1_700_000_000_000
    for i in range(n):
        px = px * (1.0 + 0.002 * math.sin(i / 7.0) + 0.0005 * ((i % 5) - 2))
        out.append(
            OHLCV(
                timestamp_ms=ts + i * 900_000,
                open=px,
                high=px * 1.002,
                low=px * 0.998,
                close=px,
                volume=1000.0 + (i % 10) * 50,
            )
        )
    return out


def test_ohlcv_store_append_dedupe(tmp_path: Path):
    store = OhlcvStore(tmp_path / "ohlcv")
    candles = _synth_candles(30)
    assert store.append("BTC/IDR", "15m", candles) == 30
    assert store.append("BTC/IDR", "15m", candles) == 0
    loaded = store.load("BTC/IDR", "15m")
    assert len(loaded) == 30


def test_features_causal_length():
    rows = build_feature_matrix(_synth_candles(80))
    assert len(rows) > 10
    assert 0.0 <= rows[-1].rsi_14 <= 1.0


def test_labels_no_future_at_tail():
    candles = _synth_candles(80)
    rows = build_feature_matrix(candles)
    labels = label_forward_direction(candles, rows, LabelConfig(horizon_bars=4))
    assert len(labels) == len(rows)
    assert labels[-1] == 0


def test_baseline_backtest_runs():
    s = Settings(live_mode=True, min_quote_balance=1)
    result = run_rule_baseline(_synth_candles(150), s, horizon_bars=4)
    assert result.metrics.n_bars > 0
    assert result.metrics.max_drawdown >= 0.0
    assert result.name == "btc_rule_baseline"


def test_ml_filter_disabled_passthrough():
    f = MlSignalFilter(enabled=False)
    out = f.filter(TradeDecision(Signal.BUY, "x", 0.5, 100.0), _synth_candles(50))
    assert out.allow is True


def test_ml_filter_enabled_without_model_blocks_buy():
    f = MlSignalFilter(enabled=True, model=None)
    out = f.filter(TradeDecision(Signal.BUY, "x", 0.5, 100.0), _synth_candles(50))
    assert out.allow is False


def test_ml_filter_enabled_allows_sell_without_model():
    f = MlSignalFilter(enabled=True, model=None)
    out = f.filter(TradeDecision(Signal.SELL, "x", 0.5, 100.0), _synth_candles(50))
    assert out.allow is True


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("sklearn") is None,
    reason="scikit-learn optional",
)
def test_walk_forward_with_sklearn():
    from tko.ml.features import rows_to_xy
    from tko.ml.walk_forward import walk_forward_classify

    candles = _synth_candles(400)
    rows = build_feature_matrix(candles)
    labels = label_forward_direction(
        candles, rows, LabelConfig(horizon_bars=3, up_threshold=0.0005, down_threshold=-0.0005)
    )
    x, y = rows_to_xy(rows, labels)
    if sum(1 for yi in y if yi in (-1, 1)) < 250:
        pytest.skip("not enough directional labels")
    wf = walk_forward_classify(x, y, kind="logreg", train_bars=80, test_bars=20, step_bars=20)
    assert wf.mean_accuracy >= 0.0


def test_strategy_analyze_returns_trade_decision():
    from tko.strategy.btc import BtcAnalyzer

    d = BtcAnalyzer(Settings(live_mode=True, min_quote_balance=1)).analyze(_synth_candles(80))
    assert isinstance(d, TradeDecision)
    assert d.signal in (Signal.BUY, Signal.SELL, Signal.HOLD)
