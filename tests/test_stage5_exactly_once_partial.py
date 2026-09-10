"""S5-B3: cumulative partial-fill must account delta exactly once."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side
from tko.execution.engine import ExecutionEngine
from tko.execution.fill_journal import FillJournal
from tko.execution.intent import IntentStore
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore


def _settings(**kw) -> Settings:
    base = dict(
        max_order_notional=5_000_000,
        max_daily_notional=10_000_000,
        max_position_pct=100,
        min_quote_balance=1,
        max_open_positions=10,
    )
    base.update(kw)
    return Settings(**base)


def _engine(tmp_path: Path) -> ExecutionEngine:
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


def _result(*, filled: float, remaining: float, status: str, avg: float = 1_000_000.0) -> OrderResult:
    return OrderResult(
        id="ex-1",
        symbol="BTC/IDR",
        side=Side.BUY,
        type=OrderType.MARKET,
        amount=1.0,
        price=avg,
        status=status,
        filled=filled,
        remaining=remaining,
        average=avg,
        client_order_id="cid-1",
    )


def test_cumulative_partial_exactly_once(tmp_path: Path):
    """Sequence 0.4 → 0.4 → 0.7 → 0.7 → 1.0 accounts only deltas."""
    eng = _engine(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    assert eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)[0]

    steps = [
        (0.4, 0.6, "open", True),
        (0.4, 0.6, "open", True),
        (0.7, 0.3, "partial", True),
        (0.7, 0.3, "partial", True),
        (1.0, 0.0, "closed", False),
    ]
    for filled, remaining, status, partial in steps:
        intent.filled = filled
        res = _result(filled=filled, remaining=remaining, status=status)
        eng._on_fill_confirmed(
            intent, Side.BUY, res, base="BTC", quote="IDR",
            partial=partial, remaining=remaining,
        )
        intent = eng.intents.by_client_id(intent.client_order_id)

    pos = eng.positions_store.get("BTC/IDR")
    assert pos is not None
    assert pos.amount == pytest.approx(1.0)
    assert eng.risk.pnl.today_notional() == pytest.approx(1_000_000)
    assert eng.risk.reserved_notional() == pytest.approx(0.0)
    assert intent.accounted_filled == pytest.approx(1.0)


def test_duplicate_cumulative_is_noop(tmp_path: Path):
    eng = _engine(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=500_000, last_price=1_000_000
    )
    intent.normalized_base = 0.5
    eng.risk.try_reserve_notional(500_000, reservation_id=intent.client_order_id)

    res = _result(filled=0.3, remaining=0.2, status="open", avg=1_000_000)
    eng._on_fill_confirmed(intent, Side.BUY, res, base="BTC", quote="IDR", partial=True, remaining=0.2)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.accounted_filled == pytest.approx(0.3)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.3)
    assert eng.risk.pnl.today_notional() == pytest.approx(300_000)

    eng._on_fill_confirmed(intent, Side.BUY, res, base="BTC", quote="IDR", partial=True, remaining=0.2)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.accounted_filled == pytest.approx(0.3)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.3)
    assert eng.risk.pnl.today_notional() == pytest.approx(300_000)


def test_partial_rereserve_fail_closed_clamps(tmp_path: Path):
    s = _settings(max_order_notional=1_000_000, max_daily_notional=1_000_000)
    risk = RiskEngine(s, tmp_path, DailyPnLTracker(tmp_path / "pnl.jsonl", timezone_name="UTC"))
    risk.pnl.record_trade(side="buy", symbol="X", notional=800_000, pnl=0.0)
    risk.commit_partial_and_rereserve(
        "cid",
        side="buy",
        symbol="BTC/IDR",
        filled_notional=0.0,
        remaining_reserve=400_000,
    )
    assert risk.reserved_notional() == pytest.approx(200_000)


def test_accounted_filled_survives_intent_reload(tmp_path: Path):
    store = IntentStore(tmp_path / "intents.json")
    intent = store.create(symbol="BTC/IDR", side="buy", quote_amount=100_000, last_price=1000)
    intent.accounted_filled = 0.42
    intent.filled = 0.42
    store.update(intent)
    reloaded = IntentStore(tmp_path / "intents.json").by_client_id(intent.client_order_id)
    assert reloaded is not None
    assert reloaded.accounted_filled == pytest.approx(0.42)
