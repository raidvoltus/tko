"""S5 crash-window matrix: journal → position → watermark → PnL → mark_applied.

Each intermediate durable state must recover without double-count on restart.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side
from tko.execution.engine import ExecutionEngine
from tko.execution.fill_journal import FillEvent, FillJournal, make_event_id
from tko.execution.intent import IntentStore
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore


def _settings() -> Settings:
    return Settings(
        max_order_notional=5_000_000,
        max_daily_notional=10_000_000,
        max_position_pct=100,
        min_quote_balance=1,
        max_open_positions=10,
    )


def _build(tmp_path: Path) -> ExecutionEngine:
    s = _settings()
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    eng = ExecutionEngine.__new__(ExecutionEngine)
    eng.client = MagicMock()
    eng.s = s
    eng.risk = risk
    eng.audit = eng.metrics = eng.lifecycle = None
    eng.positions_store = PositionStore(tmp_path / "positions.json")
    eng.positions = {}
    eng.intents = IntentStore(tmp_path / "intents.json")
    eng.fill_journal = FillJournal(tmp_path / "fill_events.jsonl")
    eng.reconciler = MagicMock()
    return eng


def _buy_result(filled: float, remaining: float) -> OrderResult:
    return OrderResult(
        id="ex-1", symbol="BTC/IDR", side=Side.BUY, type=OrderType.MARKET,
        amount=1.0, price=1_000_000.0, status="open" if remaining > 0 else "closed",
        filled=filled, remaining=remaining, average=1_000_000.0, client_order_id="cid",
    )


def test_A_crash_before_journal_nothing_durable(tmp_path: Path):
    eng = _build(tmp_path)
    assert eng.fill_journal.unapplied_events() == []
    assert eng.positions_store.get("BTC/IDR") is None

    eng2 = _build(tmp_path)
    intent2 = eng2.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    eng2.risk.try_reserve_notional(1_000_000, reservation_id=intent2.client_order_id)
    intent2.normalized_base = 1.0
    eng2._on_fill_confirmed(
        intent2, Side.BUY, _buy_result(0.4, 0.6), base="BTC", quote="IDR",
        partial=True, remaining=0.6,
    )
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)


def test_B_crash_after_journal_before_position(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)

    event_id = make_event_id(intent.client_order_id, 0.4)
    event = FillEvent(
        event_id=event_id, client_order_id=intent.client_order_id,
        symbol="BTC/IDR", side="buy", delta=0.4, cumulative=0.4,
        average=1_000_000.0, notional=400_000.0, order_id="ex-1",
        partial=True, remaining=0.6, quote_amount=1_000_000.0,
    )
    assert eng.fill_journal.try_record(event) is True
    assert eng.positions_store.get("BTC/IDR") is None

    eng2 = _build(tmp_path)
    eng2._replay_unapplied_fills()
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)
    assert eng2.fill_journal.is_applied(event_id)


def test_C_crash_after_position_before_watermark(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    eng._on_fill_confirmed(
        intent, Side.BUY, _buy_result(0.4, 0.6), base="BTC", quote="IDR",
        partial=True, remaining=0.6,
    )
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.accounted_filled == pytest.approx(0.4)

    intent.accounted_filled = 0.0
    eng.intents.update(intent)

    eng._on_fill_confirmed(
        intent, Side.BUY, _buy_result(0.4, 0.6), base="BTC", quote="IDR",
        partial=True, remaining=0.6,
    )
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng.risk.pnl.today_notional() == pytest.approx(400_000)


def test_D_pnl_write_failure_leaves_unapplied(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    event_id = make_event_id(intent.client_order_id, 0.4)

    with patch.object(eng.risk.pnl, "record_trade", side_effect=OSError("disk full")), pytest.raises(OSError):
        eng._on_fill_confirmed(
            intent, Side.BUY, _buy_result(0.4, 0.6), base="BTC", quote="IDR",
            partial=True, remaining=0.6,
        )
    assert eng.fill_journal.has_event(event_id)
    assert eng.fill_journal.is_applied(event_id) is False

    eng2 = _build(tmp_path)
    eng2._replay_unapplied_fills()
    assert eng2.fill_journal.is_applied(event_id)
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)


def test_E_crash_after_pnl_before_mark_applied(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)

    event_id = make_event_id(intent.client_order_id, 0.4)
    event = FillEvent(
        event_id=event_id, client_order_id=intent.client_order_id,
        symbol="BTC/IDR", side="buy", delta=0.4, cumulative=0.4,
        average=1_000_000.0, notional=400_000.0, order_id="ex-1",
        partial=True, remaining=0.6, quote_amount=1_000_000.0,
    )
    eng.fill_journal.try_record(event)
    eng.positions_store.upsert(
        symbol="BTC/IDR", base="BTC", quote="IDR", amount=0.4, entry_price=1_000_000.0,
        fill_event_id=event_id,
    )
    intent.accounted_filled = 0.4
    eng.intents.update(intent)
    eng.risk.pnl.record_trade(
        side="buy", symbol="BTC/IDR", notional=400_000.0, fill_event_id=event_id,
    )
    assert eng.fill_journal.is_applied(event_id) is False

    eng2 = _build(tmp_path)
    eng2._replay_unapplied_fills()
    assert eng2.fill_journal.is_applied(event_id)
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)


def test_F_restart_same_cumulative_no_double(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    for filled, rem, partial in [(0.4, 0.6, True), (0.4, 0.6, True), (0.7, 0.3, True), (1.0, 0.0, False)]:
        eng._on_fill_confirmed(
            intent, Side.BUY, _buy_result(filled, rem), base="BTC", quote="IDR",
            partial=partial, remaining=rem,
        )
        intent = eng.intents.by_client_id(intent.client_order_id)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    assert eng.risk.pnl.today_notional() == pytest.approx(1_000_000)


def test_sell_reduce_idempotent_on_replay(tmp_path: Path):
    eng = _build(tmp_path)
    eng.positions_store.upsert(
        symbol="BTC/IDR", base="BTC", quote="IDR", amount=1.0, entry_price=1_000_000.0,
    )
    eng.positions["BTC/IDR"] = eng.positions_store.get("BTC/IDR")  # type: ignore

    intent = eng.intents.create(
        symbol="BTC/IDR", side="sell", base_amount=0.4, last_price=1_100_000
    )
    intent.normalized_base = 0.4
    event_id = make_event_id(intent.client_order_id, 0.4)
    event = FillEvent(
        event_id=event_id, client_order_id=intent.client_order_id,
        symbol="BTC/IDR", side="sell", delta=0.4, cumulative=0.4,
        average=1_100_000.0, notional=440_000.0, order_id="ex-s",
    )
    eng.fill_journal.try_record(event)
    eng._apply_fill_event(event, intent=intent, base="BTC", quote="IDR")
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.6)
    assert eng.fill_journal.is_applied(event_id)

    eng.fill_journal._applied.discard(event_id)
    eng.fill_journal._save_applied()
    eng._replay_unapplied_fills()
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.6)
