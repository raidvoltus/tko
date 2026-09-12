"""Stage 5: partial-fill accounting + reservation crash recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from tko.core.config import Settings
from tko.core.types import Side
from tko.execution.intent import (
    BLOCKS_DUPLICATE,
    IntentStore,
    OrderIntentStatus,
)
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker


def _settings(**kwargs) -> Settings:
    base = {
        "max_order_notional": 5_000_000,
        "max_daily_notional": 10_000_000,
        "max_position_pct": 100,
        "min_quote_balance": 1,
        "max_open_positions": 10,
    }
    base.update(kwargs)
    return Settings(**base)


def test_partially_filled_blocks_duplicate(tmp_path: Path):
    assert OrderIntentStatus.PARTIALLY_FILLED in BLOCKS_DUPLICATE
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1000)
    intent.status = OrderIntentStatus.PARTIALLY_FILLED
    intent.filled = 0.4
    store.update(intent)
    assert store.create_if_absent(symbol="BTC/IDR", side="buy", quote_amount=1) is None


def test_unresolved_for_recovery_includes_submitting(tmp_path: Path):
    store = IntentStore(tmp_path / "intents.json")
    a = store.create(symbol="BTC/IDR", side="buy", quote_amount=100_000, last_price=1000)
    a.status = OrderIntentStatus.SUBMITTING
    store.update(a)
    b = store.create(symbol="ETH/IDR", side="buy", quote_amount=50_000, last_price=100)
    b.status = OrderIntentStatus.UNKNOWN
    store.update(b)
    unk = {i.client_order_id for i in store.unresolved_unknown()}
    assert a.client_order_id not in unk
    assert b.client_order_id in unk
    rec = {i.client_order_id for i in store.unresolved_for_recovery()}
    assert a.client_order_id in rec
    assert b.client_order_id in rec


def test_rehydrate_reservations_from_submitting_intent(tmp_path: Path):
    s = _settings(max_daily_notional=5_000_000)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=800_000, last_price=1000)
    intent.status = OrderIntentStatus.SUBMITTING
    store.update(intent)
    assert risk.reserved_notional() == 0
    n = risk.rehydrate_reservations_from_intents(store.buy_intents_holding_budget())
    assert n == 1
    assert risk.reserved_notional() == pytest.approx(800_000)
    ok, _ = risk.try_reserve_notional(4_000_000, reservation_id="other")
    assert ok
    ok2, _ = risk.try_reserve_notional(500_000, reservation_id="over")
    assert not ok2


def test_rehydrate_partial_uses_residual(tmp_path: Path):
    s = _settings(max_daily_notional=5_000_000)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000)
    intent.status = OrderIntentStatus.PARTIALLY_FILLED
    intent.filled = 0.4
    intent.average = 1_000_000
    store.update(intent)
    risk.rehydrate_reservations_from_intents(store.buy_intents_holding_budget())
    assert risk.reserved_notional() == pytest.approx(600_000)


def test_result_from_intent_preserves_partial_status(tmp_path: Path):
    from tko.execution.engine import ExecutionEngine

    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1000)
    intent.status = OrderIntentStatus.PARTIALLY_FILLED
    intent.filled = 0.4
    intent.normalized_base = 1.0
    intent.average = 1000.0
    intent.exchange_order_id = "ex-1"
    store.update(intent)
    eng = ExecutionEngine.__new__(ExecutionEngine)
    eng.intents = store
    res = ExecutionEngine._result_from_intent(eng, intent, Side.BUY)
    assert res.status == "open"
    assert res.filled == pytest.approx(0.4)
    assert res.remaining == pytest.approx(0.6)


def test_confirmed_result_is_closed(tmp_path: Path):
    from tko.execution.engine import ExecutionEngine

    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=100_000, last_price=1000)
    intent.status = OrderIntentStatus.CONFIRMED
    intent.filled = 0.1
    intent.normalized_base = 0.1
    intent.average = 1000.0
    eng = ExecutionEngine.__new__(ExecutionEngine)
    res = ExecutionEngine._result_from_intent(eng, intent, Side.BUY)
    assert res.status == "closed"
    assert res.remaining == 0.0


def test_commit_partial_and_rereserve(tmp_path: Path):
    s = _settings(max_order_notional=1_000_000, max_daily_notional=2_000_000)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    assert risk.try_reserve_notional(1_000_000, reservation_id="cid-1")[0]
    risk.commit_partial_and_rereserve(
        "cid-1",
        side="buy",
        symbol="BTC/IDR",
        filled_notional=400_000,
        remaining_reserve=600_000,
        order_id="ex",
    )
    assert risk.pnl.today_notional() == pytest.approx(400_000)
    assert risk.reserved_notional() == pytest.approx(600_000)
