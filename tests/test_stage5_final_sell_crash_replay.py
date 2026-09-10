"""S5 final regression: SELL crash before mark_applied → replay exactly-once.

Invariant
---------
position = 1.0
SELL delta = 0.4
first apply → store=0.6, memory=0.6, PnL=440k, applied=True
simulate crash before mark_applied
restart/replay → store=0.6, memory=0.6, PnL=440k, applied=True

Forbidden: store=0.2 or PnL=880k (double reduce / double PnL).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.execution.engine import ExecutionEngine, PositionState
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


def test_final_sell_crash_before_mark_applied_replay_exactly_once(tmp_path: Path):
    eng = _build(tmp_path)

    # Seed long position = 1.0 BTC @ 1_000_000
    eng.positions_store.upsert(
        symbol="BTC/IDR",
        base="BTC",
        quote="IDR",
        amount=1.0,
        entry_price=1_000_000.0,
    )
    seed = eng.positions_store.get("BTC/IDR")
    assert seed is not None and seed.amount == pytest.approx(1.0)
    eng.positions["BTC/IDR"] = PositionState(
        symbol=seed.symbol,
        base=seed.base,
        quote=seed.quote,
        amount=seed.amount,
        entry_price=seed.entry_price,
        opened_at=seed.opened_at,
    )

    intent = eng.intents.create(
        symbol="BTC/IDR",
        side="sell",
        base_amount=0.4,
        last_price=1_100_000.0,
    )
    intent.normalized_base = 0.4

    event_id = make_event_id(intent.client_order_id, 0.4)
    event = FillEvent(
        event_id=event_id,
        client_order_id=intent.client_order_id,
        symbol="BTC/IDR",
        side="sell",
        delta=0.4,
        cumulative=0.4,
        average=1_100_000.0,
        notional=440_000.0,
        order_id="ex-sell-1",
    )

    # --- first apply (normal path) ---
    assert eng.fill_journal.try_record(event) is True
    eng._apply_fill_event(event, intent=intent, base="BTC", quote="IDR")

    store_pos = eng.positions_store.get("BTC/IDR")
    assert store_pos is not None
    assert store_pos.amount == pytest.approx(0.6)
    assert eng.positions["BTC/IDR"].amount == pytest.approx(0.6)
    assert eng.risk.pnl.today_notional() == pytest.approx(440_000.0)
    assert eng.fill_journal.is_applied(event_id) is True

    # --- simulate crash before mark_applied: durable side-effects remain,
    #     journal applied-set is rolled back ---
    eng.fill_journal._applied.discard(event_id)
    eng.fill_journal._save_applied()
    assert eng.fill_journal.is_applied(event_id) is False
    assert eng.fill_journal.has_event(event_id) is True

    # --- restart: new process, same state dir ---
    eng2 = _build(tmp_path)
    eng2._hydrate_positions_memory()
    eng2._replay_unapplied_fills()

    store2 = eng2.positions_store.get("BTC/IDR")
    assert store2 is not None
    assert store2.amount == pytest.approx(0.6), "must not double-reduce to 0.2"
    assert eng2.positions.get("BTC/IDR") is not None
    assert eng2.positions["BTC/IDR"].amount == pytest.approx(0.6)
    assert eng2.risk.pnl.today_notional() == pytest.approx(440_000.0), "must not double PnL to 880k"
    assert eng2.fill_journal.is_applied(event_id) is True

    # Second replay must remain a no-op
    eng2._replay_unapplied_fills()
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.6)
    assert eng2.risk.pnl.today_notional() == pytest.approx(440_000.0)
