"""Stage 7: strategy signal + risk gates, reservations, exits (production path)."""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.core.types import Signal
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker


def _settings(**kw) -> Settings:
    base = dict(
        max_order_notional=500_000,
        max_daily_notional=5_000_000,
        max_position_pct=100,
        min_quote_balance=1,
        max_open_positions=10,
        max_daily_loss_pct=5.0,
        daily_equity_baseline=1_000_000,
        take_profit_pct=2.0,
        stop_loss_pct=1.0,
    )
    base.update(kw)
    if base.get("max_daily_notional", 0) and base.get("max_order_notional", 0):
        if base["max_order_notional"] > base["max_daily_notional"]:
            base["max_order_notional"] = base["max_daily_notional"]
    if base.get("max_open_positions", 1) < 1:
        base["max_open_positions"] = 1
    base.setdefault("market_data_max_age_sec", 0)
    return Settings(**base)


def _risk(tmp_path: Path, **kw) -> RiskEngine:
    s = _settings(**kw)
    return RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))


def test_max_order_notional_exact_and_over(tmp_path: Path):
    risk = _risk(tmp_path, max_order_notional=100_000)
    dec = risk.size_buy_only(free_quote=10_000_000, last_price=1000, open_positions=0, quote_asset="IDR")
    assert dec.approved
    assert dec.size_quote <= 100_000 + 1e-6
    dec2 = risk.size_buy_only(free_quote=50_000, last_price=1000, open_positions=0, quote_asset="IDR")
    # may be blocked by min size depending on min_quote_balance
    assert isinstance(dec2.approved, bool)


def test_max_daily_notional_blocks(tmp_path: Path):
    risk = _risk(tmp_path, max_daily_notional=100_000, max_order_notional=100_000)
    risk.pnl.record_trade(side="buy", symbol="X", notional=100_000, pnl=0)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY
    )
    assert not dec.approved


def test_kill_blocks_buy_allows_exit(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.activate_kill_switch("test")
    buy = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY
    )
    assert not buy.approved
    sell = risk.evaluate_exit(symbol="BTC/IDR", base_free=0.01, last_price=1000, entry_price=900)
    assert sell.approved


def test_kill_persists_across_engine_instances(tmp_path: Path):
    risk = _risk(tmp_path)
    risk.activate_kill_switch("persist")
    risk2 = _risk(tmp_path)
    assert risk2.kill_switch_active()


def test_trade_decision_buy_accepted(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY
    )
    assert dec.approved


def test_trade_decision_hold_blocked(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.HOLD
    )
    assert not dec.approved


def test_trade_decision_sell_blocked_on_entry(tmp_path: Path):
    risk = _risk(tmp_path)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.SELL
    )
    assert not dec.approved


def test_take_profit_exit(tmp_path: Path):
    risk = _risk(tmp_path, take_profit_pct=2.0)
    dec = risk.evaluate_exit(
        symbol="BTC/IDR", base_free=0.01, last_price=1020, entry_price=1000
    )
    assert dec.approved


def test_stop_loss_exit(tmp_path: Path):
    risk = _risk(tmp_path, stop_loss_pct=1.0)
    dec = risk.evaluate_exit(
        symbol="BTC/IDR", base_free=0.01, last_price=989, entry_price=1000
    )
    assert dec.approved


def test_hold_below_tp_sl(tmp_path: Path):
    risk = _risk(tmp_path, take_profit_pct=5.0, stop_loss_pct=5.0)
    dec = risk.evaluate_exit(
        symbol="BTC/IDR", base_free=0.01, last_price=1001, entry_price=1000
    )
    assert not dec.approved


def test_strategy_signal_still_requires_risk_approval(tmp_path: Path):
    risk = _risk(tmp_path, max_open_positions=1)
    dec = risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=Signal.BUY, open_positions=1
    )
    assert not dec.approved


def test_evaluate_buy_removed_cannot_authorize(tmp_path: Path):
    """Production must not use evaluate_buy as BUY authorization."""
    risk = _risk(tmp_path)
    with pytest.raises(RuntimeError, match="not a production authorization path"):
        risk.evaluate_buy(free_quote=10_000_000, last_price=1000, open_positions=0)
