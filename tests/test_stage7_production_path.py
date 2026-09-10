"""Stage 7 production-path: strategy → evaluate_entry → buy; HOLD/SELL never submit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side, Signal
from tko.execution.engine import ExecutionEngine
from tko.execution.fill_journal import FillJournal
from tko.execution.intent import IntentStore
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore
from tko.strategy.btc import TradeDecision


def _settings(**kw) -> Settings:
    base = dict(
        max_order_notional=1_000_000,
        max_daily_notional=5_000_000,
        max_position_pct=50,
        min_quote_balance=1,
        max_open_positions=5,
        max_daily_loss_pct=5.0,
        base_asset="BTC",
        quote_asset="IDR",
    )
    base.update(kw)
    return Settings(**base)


def _engine(tmp_path: Path, client=None) -> ExecutionEngine:
    s = _settings()
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    eng = ExecutionEngine.__new__(ExecutionEngine)
    eng.client = client or MagicMock()
    eng.s = s
    eng.risk = risk
    eng.audit = eng.metrics = None
    eng.lifecycle = MagicMock()
    eng.lifecycle.run_authorized_submit = lambda fn: fn()
    eng.positions_store = PositionStore(tmp_path / "positions.json")
    eng.positions = {}
    eng.intents = IntentStore(tmp_path / "intents.json")
    eng.fill_journal = FillJournal(tmp_path / "fill_events.jsonl")
    eng.reconciler = MagicMock()
    return eng


def test_A_trade_decision_buy_reaches_evaluate_entry_then_buy(tmp_path: Path):
    eng = _engine(tmp_path)
    eng.client.validate_symbol_ready = MagicMock(return_value=(True, "ok"))
    eng.client.circuit_open = False
    constraints = MagicMock()
    constraints.validate_notional = MagicMock(return_value=(True, "ok"))
    eng.client.get_constraints = MagicMock(return_value=constraints)
    eng.client.create_order = MagicMock(
        return_value=OrderResult(
            id="ex1", symbol="BTC/IDR", side=Side.BUY, type=OrderType.MARKET,
            amount=0.001, price=1e6, status="closed", filled=0.001, remaining=0.0,
            average=1e6, client_order_id="x",
        )
    )

    td = TradeDecision(Signal.BUY, "ema cross", 0.8, 1_000_000.0)
    decision = eng.risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1_000_000,
        signal=td, open_positions=0, quote_asset="IDR",
    )
    assert decision.approved

    result = eng.buy("BTC/IDR", "BTC", "IDR", decision, 1_000_000.0)
    assert result is not None
    eng.client.create_order.assert_called()


def test_B_hold_never_submits_order(tmp_path: Path):
    eng = _engine(tmp_path)
    eng.client.create_order = MagicMock()
    td = TradeDecision(Signal.HOLD, "flat", 0.0, 1_000_000.0)
    decision = eng.risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1_000_000, signal=td
    )
    assert not decision.approved
    eng.client.create_order.assert_not_called()


def test_C_sell_signal_never_submits_buy(tmp_path: Path):
    eng = _engine(tmp_path)
    eng.client.create_order = MagicMock()
    td = TradeDecision(Signal.SELL, "exit", 0.7, 1_000_000.0)
    decision = eng.risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1_000_000, signal=td
    )
    assert not decision.approved
    eng.client.create_order.assert_not_called()


def test_D_invalid_strength_blocked_no_order(tmp_path: Path):
    eng = _engine(tmp_path)
    eng.client.create_order = MagicMock()
    td = TradeDecision(Signal.BUY, "bad", float("nan"), 1_000_000.0)
    decision = eng.risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1_000_000, signal=td
    )
    assert not decision.approved
    eng.client.create_order.assert_not_called()


def test_E_F_nan_price_and_quote_blocked(tmp_path: Path):
    eng = _engine(tmp_path)
    td = TradeDecision(Signal.BUY, "x", 0.5, 1_000_000.0)
    for price in (float("nan"), float("inf"), 0.0, -1.0):
        d = eng.risk.evaluate_entry(
            symbol="BTC/IDR", quote_free=10_000_000, last_price=price, signal=td
        )
        assert not d.approved
    d = eng.risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=float("nan"), last_price=1_000_000, signal=td
    )
    assert not d.approved


def test_G_nan_reservation_blocked(tmp_path: Path):
    eng = _engine(tmp_path)
    ok, _ = eng.risk.try_reserve_notional(float("nan"), reservation_id="x")
    assert not ok
    assert eng.risk.reserved_notional() == 0.0


def test_I_strategy_buy_cannot_bypass_daily_cap(tmp_path: Path):
    eng = _engine(tmp_path)
    s = _settings(max_daily_notional=100_000, max_order_notional=100_000)
    eng.risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl2.jsonl", timezone_name="UTC"))
    eng.risk.pnl.record_trade(side="buy", symbol="X", notional=100_000, pnl=0)
    td = TradeDecision(Signal.BUY, "strong", 1.0, 1000.0)
    d = eng.risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=td
    )
    assert not d.approved
    eng.client.create_order = MagicMock()
    eng.client.create_order.assert_not_called()


def test_J_K_kill_blocks_buy_allows_force_sell(tmp_path: Path):
    eng = _engine(tmp_path)
    eng.risk.activate_kill_switch("test")
    td = TradeDecision(Signal.BUY, "x", 0.9, 1000.0)
    d = eng.risk.evaluate_entry(
        symbol="BTC/IDR", quote_free=10_000_000, last_price=1000, signal=td
    )
    assert not d.approved
    sell = eng.risk.evaluate_sell(0.5, 1000.0, 900.0, False)
    assert sell.approved and sell.size_base == pytest.approx(0.5)
