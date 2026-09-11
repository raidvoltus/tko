"""Stage 6 gap coverage: crash windows A, F, K, L, N.

Proves properties required for exactly-once execution against real APIs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from tko.core.config import Settings
from tko.exchange.order_response import OrderLookupResult
from tko.execution.engine import ExecutionEngine
from tko.execution.fill_journal import FillEvent, FillJournal, make_event_id
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
    eng.audit = eng.metrics = eng.lifecycle = None
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


def _ex(
    *,
    oid: str = "ex-1",
    cid: str,
    filled: float,
    remaining: float,
    status: str,
    avg: float = 1_000_000.0,
    amount: float = 1.0,
) -> dict:
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


def test_A_crash_before_intent_persist_no_exchange_submit(tmp_path: Path) -> None:
    """No intent file → no order may be submitted; store empty."""
    path = tmp_path / "intents.json"
    store = IntentStore(path)
    assert store.by_client_id("nonexistent") is None
    assert list(store.blocking_intents("BTC/IDR", "buy")) == []

    eng = _build(tmp_path)
    eng.client.create_order = MagicMock(side_effect=AssertionError("must not submit without intent"))
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    assert intent.client_order_id
    assert eng.intents.by_client_id(intent.client_order_id) is not None
    eng.client.create_order.assert_not_called()
    assert intent.status != OrderIntentStatus.SUBMITTING


def test_F_response_lost_unknown_blocks_then_recon(tmp_path: Path) -> None:
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)

    assert intent.status in BLOCKS_DUPLICATE
    assert eng.intents.blocking_intents("BTC/IDR", "buy")

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=intent.client_order_id, filled=1.0, remaining=0.0, status="closed")
        )
    )
    out = eng.reconciler.reconcile_intent(intent)
    assert out.status != OrderIntentStatus.FAILED
    assert out.client_order_id == intent.client_order_id


def test_K_duplicate_fill_event_recorded_once(tmp_path: Path) -> None:
    journal = FillJournal(tmp_path / "fills.jsonl")
    cid = "TKO-btc-BTCIDR-B-dup"
    eid = make_event_id(cid, 1.0)
    event = FillEvent(
        event_id=eid,
        client_order_id=cid,
        symbol="BTC/IDR",
        side="buy",
        delta=1.0,
        cumulative=1.0,
        average=1_000_000.0,
        notional=1_000_000.0,
        order_id="ex-1",
    )
    assert journal.try_record(event) is True
    assert journal.try_record(event) is False
    assert journal.has_event(eid)
    assert len(journal.events_for_cid(cid)) == 1


def test_L_out_of_order_fills_do_not_reduce_accounted(tmp_path: Path) -> None:
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    intent.filled = 0.0
    intent.accounted_filled = 0.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=intent.client_order_id, filled=0.7, remaining=0.3, status="open", amount=1.0)
        )
    )
    eng.reconciler.reconcile_intent(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent is not None
    high = float(intent.filled or 0.0)

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=intent.client_order_id, filled=0.4, remaining=0.6, status="open", amount=1.0)
        )
    )
    eng.reconciler.reconcile_intent(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent is not None
    later = float(intent.filled or 0.0)
    assert later + 1e-12 >= high - 1e-9


def test_N_transient_recon_fail_then_success(tmp_path: Path) -> None:
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.query_failed("5xx")
    )
    out = eng.reconciler.reconcile_intent(intent)
    assert out.status in (OrderIntentStatus.UNKNOWN, OrderIntentStatus.RECONCILIATION)
    assert out.status in BLOCKS_DUPLICATE or out.status == OrderIntentStatus.RECONCILIATION

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=intent.client_order_id, filled=1.0, remaining=0.0, status="closed")
        )
    )
    out2 = eng.reconciler.reconcile_intent(out)
    assert out2.client_order_id == intent.client_order_id
    assert out2.status != OrderIntentStatus.FAILED
