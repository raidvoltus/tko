"""Stage 6-H + restart: order placement exactly-once + durable replay.

H  — after TIMEOUT → UNKNOWN, second _submit / buy must NOT call create_order again
     (create_order.call_count == 1). create_if_absent must return None.
Restart — IntentStore + FillJournal + PositionStore reloaded from disk;
     second process reconciles without double-counting position/PnL.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.core.types import Side
from tko.exchange.order_response import OrderLookupResult
from tko.exchange.tokocrypto import TokocryptoError
from tko.execution.engine import ExecutionEngine
from tko.execution.errors import ErrorCategory
from tko.execution.fill_journal import FillJournal
from tko.execution.intent import BLOCKS_DUPLICATE, IntentStore, OrderIntentStatus
from tko.reconciliation.reconciler import Reconciler
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore


def _settings() -> Settings:
    return Settings(
        live_mode=True,
        max_order_notional=5_000_000,
        max_daily_notional=10_000_000,
        max_position_pct=100,
        min_quote_balance=1,
        max_open_positions=10,
    )


def _build(tmp_path: Path, client: MagicMock | None = None) -> ExecutionEngine:
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
    eng.reconciler = Reconciler(
        client=eng.client,
        intents=eng.intents,
        positions=eng.positions_store,
        max_unknown_checks=5,
    )
    return eng


def _ex(*, cid: str, filled: float = 1.0, remaining: float = 0.0, status: str = "closed",
        avg: float = 1_000_000.0, amount: float = 1.0, oid: str = "ex-1") -> dict:
    return {
        "id": oid,
        "clientOrderId": cid,
        "symbol": "BTC/IDR",
        "side": "buy",
        "status": status,
        "filled": filled,
        "remaining": remaining,
        "amount": amount,
        "average": avg,
        "price": avg,
    }


def test_H_second_submit_after_timeout_does_not_repost(tmp_path: Path) -> None:
    """TIMEOUT leaves UNKNOWN; second _submit must not call create_order again."""
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    intent.status = OrderIntentStatus.NORMALIZED
    eng.intents.update(intent)
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)

    create = MagicMock(
        side_effect=TokocryptoError(
            "request timeout", category=ErrorCategory.TIMEOUT, ambiguous=True
        )
    )
    eng.client.create_order = create
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.query_failed("timeout")
    )

    r1 = eng._submit(
        intent, side=Side.BUY, base_amount=0.0, quote_amount=1_000_000.0,
        base="BTC", quote="IDR",
    )
    assert r1 is None
    assert create.call_count == 1
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status in (OrderIntentStatus.UNKNOWN, OrderIntentStatus.RECONCILIATION)
    assert intent.status in BLOCKS_DUPLICATE or intent.status == OrderIntentStatus.RECONCILIATION

    r2 = eng._submit(
        intent, side=Side.BUY, base_amount=0.0, quote_amount=1_000_000.0,
        base="BTC", quote="IDR",
    )
    assert r2 is None
    assert create.call_count == 1, f"create_order called {create.call_count} times — must be 1"


def test_H_create_if_absent_blocks_new_intent_while_unknown(tmp_path: Path) -> None:
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    assert intent.status in BLOCKS_DUPLICATE

    second = eng.intents.create_if_absent(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    assert second is None


def test_H_timeout_then_recon_fill_then_submit_still_no_repost(tmp_path: Path) -> None:
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    intent.status = OrderIntentStatus.NORMALIZED
    eng.intents.update(intent)
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)

    create = MagicMock(
        side_effect=TokocryptoError(
            "timeout", category=ErrorCategory.TIMEOUT, ambiguous=True
        )
    )
    eng.client.create_order = create
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.query_failed("timeout")
    )
    eng._submit(
        intent, side=Side.BUY, base_amount=0.0, quote_amount=1_000_000.0,
        base="BTC", quote="IDR",
    )
    assert create.call_count == 1

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=intent.client_order_id, filled=1.0, remaining=0.0, status="closed")
        )
    )
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.CONFIRMED
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)

    eng._submit(
        intent, side=Side.BUY, base_amount=0.0, quote_amount=1_000_000.0,
        base="BTC", quote="IDR",
    )
    assert create.call_count == 1
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)


def test_restart_reload_reconcile_no_double_accounting(tmp_path: Path) -> None:
    eng_a = _build(tmp_path)
    intent = eng_a.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    cid = intent.client_order_id
    intent.normalized_base = 1.0
    eng_a.risk.try_reserve_notional(1_000_000, reservation_id=cid)
    intent.status = OrderIntentStatus.UNKNOWN
    eng_a.intents.update(intent)

    eng_a.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=cid, filled=1.0, remaining=0.0, status="closed")
        )
    )
    eng_a._reconcile(intent)
    intent = eng_a.intents.by_client_id(cid)
    assert intent.status == OrderIntentStatus.CONFIRMED
    assert float(intent.accounted_filled) == pytest.approx(1.0)
    assert eng_a.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    notional_a = eng_a.risk.pnl.today_notional()
    assert notional_a == pytest.approx(1_000_000.0)
    journal_count_a = len(eng_a.fill_journal.events_for_cid(cid))
    assert journal_count_a == 1

    eng_b = _build(tmp_path)
    reloaded = eng_b.intents.by_client_id(cid)
    assert reloaded is not None
    assert reloaded.status == OrderIntentStatus.CONFIRMED
    assert float(reloaded.accounted_filled) == pytest.approx(1.0)

    pos_b = eng_b.positions_store.get("BTC/IDR")
    assert pos_b is not None
    assert pos_b.amount == pytest.approx(1.0)

    assert len(eng_b.fill_journal.events_for_cid(cid)) == 1

    eng_b.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=cid, filled=1.0, remaining=0.0, status="closed")
        )
    )
    eng_b._reconcile(reloaded)
    assert eng_b.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    assert eng_b.risk.pnl.today_notional() == pytest.approx(notional_a)
    assert len(eng_b.fill_journal.events_for_cid(cid)) == 1
    assert float(eng_b.intents.by_client_id(cid).accounted_filled) == pytest.approx(1.0)


def test_restart_unknown_still_blocks_and_recon_accounts_once(tmp_path: Path) -> None:
    eng_a = _build(tmp_path)
    intent = eng_a.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    cid = intent.client_order_id
    intent.normalized_base = 1.0
    eng_a.risk.try_reserve_notional(1_000_000, reservation_id=cid)
    intent.status = OrderIntentStatus.UNKNOWN
    eng_a.intents.update(intent)

    eng_b = _build(tmp_path)
    reloaded = eng_b.intents.by_client_id(cid)
    assert reloaded is not None
    assert reloaded.status == OrderIntentStatus.UNKNOWN
    assert reloaded.status in BLOCKS_DUPLICATE
    assert eng_b.intents.create_if_absent(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    ) is None

    eng_b.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=cid, filled=1.0, remaining=0.0, status="closed")
        )
    )
    eng_b._reconcile(reloaded)
    assert eng_b.intents.by_client_id(cid).status == OrderIntentStatus.CONFIRMED
    assert eng_b.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    assert len(eng_b.fill_journal.events_for_cid(cid)) == 1

    eng_c = _build(tmp_path)
    eng_c.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=cid, filled=1.0, remaining=0.0, status="closed")
        )
    )
    eng_c._reconcile(eng_c.intents.by_client_id(cid))
    assert eng_c.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    assert eng_c.risk.pnl.today_notional() == pytest.approx(1_000_000.0)
    assert len(eng_c.fill_journal.events_for_cid(cid)) == 1
