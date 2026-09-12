"""S5-B5.1: PnL durable write failure must not mark_applied."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side
from tko.execution.engine import ExecutionEngine
from tko.execution.fill_journal import FillJournal, make_event_id
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


def test_pnl_oserror_does_not_mark_applied(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)

    res = _result(0.4, 0.6, "open")
    event_id = make_event_id(intent.client_order_id, 0.4)

    # Fail only the PnL tracker's durable write
    def boom_record(*args, **kwargs):
        raise OSError("simulated disk full")

    with (
        patch.object(eng.risk.pnl, "record_trade", side_effect=boom_record),
        pytest.raises(OSError, match="simulated disk full"),
    ):
        eng._on_fill_confirmed(
            intent, Side.BUY, res, base="BTC", quote="IDR",
            partial=True, remaining=0.6,
        )

    # Journal barrier recorded; must NOT be applied
    assert eng.fill_journal.has_event(event_id)
    assert eng.fill_journal.is_applied(event_id) is False

    # Position may already be durable (before PnL) — ok; replay must not double
    eng2 = _build(tmp_path)
    eng2._replay_unapplied_fills()
    assert eng2.fill_journal.is_applied(event_id)
    pos = eng2.positions_store.get("BTC/IDR")
    assert pos is not None
    assert pos.amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)

    eng2._replay_unapplied_fills()
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)


def test_record_trade_raises_on_append_failure(tmp_path: Path):
    pnl = DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC")
    original = Path.open

    def fail_open(self, *a, **kw):
        if self.name.endswith("pnl.jsonl") or "pnl" in str(self):
            raise OSError("no space")
        return original(self, *a, **kw)

    with patch.object(Path, "open", fail_open), pytest.raises(OSError, match="no space"):
        pnl.record_trade(side="buy", symbol="BTC/IDR", notional=100.0, fill_event_id="e1")
    assert "e1" not in pnl._fill_event_ids
    assert pnl.today_notional() == 0.0
