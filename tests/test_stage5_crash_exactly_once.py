"""S5-B5: crash between durable writes must not double-count fills."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side
from tko.execution.engine import ExecutionEngine
from tko.execution.fill_journal import FillEvent, FillJournal, make_event_id
from tko.execution.intent import IntentStore
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore


def _settings(**kw) -> Settings:
    base = {
        "max_order_notional": 5_000_000,
        "max_daily_notional": 10_000_000,
        "max_position_pct": 100,
        "min_quote_balance": 1,
        "max_open_positions": 10,
    }
    base.update(kw)
    return Settings(**base)


def _build(tmp_path: Path) -> ExecutionEngine:
    s = _settings()
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    eng = ExecutionEngine.__new__(ExecutionEngine)
    eng.client = MagicMock()
    eng.s = s
    eng.risk = risk
    eng.audit = None
    eng.metrics = None
    eng.lifecycle = None
    eng.positions_store = PositionStore(tmp_path / "positions.json")
    eng.positions = {}
    eng.intents = IntentStore(tmp_path / "intents.json")
    eng.fill_journal = FillJournal(tmp_path / "fill_events.jsonl")
    eng.reconciler = MagicMock()
    return eng


def _result(filled: float, remaining: float, status: str) -> OrderResult:
    return OrderResult(
        id="ex-1", symbol="BTC/IDR", side=Side.BUY, type=OrderType.MARKET,
        amount=1.0, price=1_000_000.0, status=status, filled=filled,
        remaining=remaining, average=1_000_000.0, client_order_id="cid",
    )


def test_crash_after_journal_before_applied_replays_once(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)

    event_id = make_event_id(intent.client_order_id, 0.4)
    event = FillEvent(
        event_id=event_id,
        client_order_id=intent.client_order_id,
        symbol="BTC/IDR",
        side="buy",
        delta=0.4,
        cumulative=0.4,
        average=1_000_000.0,
        notional=400_000.0,
        order_id="ex-1",
        partial=True,
        remaining=0.6,
        quote_amount=1_000_000.0,
    )
    assert eng.fill_journal.try_record(event) is True
    assert eng.fill_journal.is_applied(event_id) is False
    assert eng.positions_store.get("BTC/IDR") is None

    eng2 = _build(tmp_path)
    eng2._replay_unapplied_fills()

    pos = eng2.positions_store.get("BTC/IDR")
    assert pos is not None
    assert pos.amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)
    assert eng2.fill_journal.is_applied(event_id)

    eng2._replay_unapplied_fills()
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)


def test_crash_after_position_before_watermark_no_double(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)

    res = _result(0.4, 0.6, "open")
    eng._on_fill_confirmed(
        intent, Side.BUY, res, base="BTC", quote="IDR", partial=True, remaining=0.6
    )
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.accounted_filled == pytest.approx(0.4)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)

    intent.accounted_filled = 0.0
    eng.intents.update(intent)

    eng._on_fill_confirmed(
        intent, Side.BUY, res, base="BTC", quote="IDR", partial=True, remaining=0.6
    )
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng.risk.pnl.today_notional() == pytest.approx(400_000)
    assert intent.accounted_filled == pytest.approx(0.4)


def test_normal_sequence_still_exactly_once(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    steps = [
        (0.4, 0.6, "open", True),
        (0.4, 0.6, "open", True),
        (0.7, 0.3, "partial", True),
        (1.0, 0.0, "closed", False),
    ]
    for filled, rem, st, partial in steps:
        res = _result(filled, rem, st)
        eng._on_fill_confirmed(
            intent, Side.BUY, res, base="BTC", quote="IDR",
            partial=partial, remaining=rem,
        )
        intent = eng.intents.by_client_id(intent.client_order_id)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    assert eng.risk.pnl.today_notional() == pytest.approx(1_000_000)
