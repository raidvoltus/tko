"""Stage 6-A/B: order reconciliation + fill reconciliation invariants."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.exchange.order_response import OrderLookupResult
from tko.execution.engine import ExecutionEngine
from tko.execution.fill_journal import FillJournal
from tko.execution.intent import BLOCKS_DUPLICATE, IntentStore, OrderIntentStatus
from tko.reconciliation.reconciler import Reconciler
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


def _exchange_order(
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


def test_A_timeout_unknown_then_recon_accounts_once(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)

    assert intent.status in BLOCKS_DUPLICATE
    assert eng.intents.create_if_absent(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    ) is None

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _exchange_order(cid=intent.client_order_id, filled=1.0, remaining=0.0, status="closed")
        )
    )
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.CONFIRMED
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    assert eng.risk.pnl.today_notional() == pytest.approx(1_000_000)

    eng._reconcile(intent)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    assert eng.risk.pnl.today_notional() == pytest.approx(1_000_000)


def test_A_query_failed_does_not_become_not_found(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=100_000, last_price=1_000_000
    )
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.query_failed("timeout 5xx")
    )
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status != OrderIntentStatus.GOVERNOR_AUTONOMOUS
    assert intent.status != OrderIntentStatus.REJECTED
    assert intent.error_category == "RECON_QUERY_FAILED"
    assert intent.status in BLOCKS_DUPLICATE


def test_A_exchange_canceled_zero_fill_is_rejected(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _exchange_order(
                cid=intent.client_order_id, filled=0.0, remaining=1.0, status="canceled"
            )
        )
    )
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.REJECTED
    assert eng.positions_store.get("BTC/IDR") is None
    assert eng.risk.pnl.today_notional() == pytest.approx(0.0)
    assert eng.risk.reserved_notional() == pytest.approx(0.0)


def test_A_not_found_to_governor_blocks_duplicate(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=100_000, last_price=1_000_000
    )
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.not_found()
    )
    for _ in range(5):
        eng._reconcile(intent, max_misses=5)
        intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.GOVERNOR_AUTONOMOUS
    assert intent.status in BLOCKS_DUPLICATE
    assert eng.intents.create_if_absent(
        symbol="BTC/IDR", side="buy", quote_amount=100_000, last_price=1_000_000
    ) is None


def test_B_partial_executed_qty(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _exchange_order(
                cid=intent.client_order_id, filled=0.4, remaining=0.6, status="open"
            )
        )
    )
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.PARTIALLY_FILLED
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng.risk.pnl.today_notional() == pytest.approx(400_000)


def test_C_recon_same_executed_qty_noop(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    order = _exchange_order(
        cid=intent.client_order_id, filled=0.4, remaining=0.6, status="partially_filled"
    )
    eng.client.find_order_by_client_id = MagicMock(return_value=OrderLookupResult.found(order))
    eng._reconcile(intent)
    eng._reconcile(intent)
    eng._reconcile(intent)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng.risk.pnl.today_notional() == pytest.approx(400_000)


def test_D_cumulative_executed_qty_incremental_delta(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _exchange_order(cid=intent.client_order_id, filled=0.4, remaining=0.6, status="open")
        )
    )
    eng._reconcile(intent)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)

    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _exchange_order(cid=intent.client_order_id, filled=0.7, remaining=0.3, status="open")
        )
    )
    eng._reconcile(intent)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.7)
    assert eng.risk.pnl.today_notional() == pytest.approx(700_000)


def test_E_restart_after_recon_fill_no_double(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1_000_000
    )
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _exchange_order(cid=intent.client_order_id, filled=0.4, remaining=0.6, status="open")
        )
    )
    eng._reconcile(intent)

    eng2 = _build(tmp_path, client=eng.client)
    eng2._replay_unapplied_fills()
    intent2 = eng2.intents.by_client_id(intent.client_order_id)
    eng2.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _exchange_order(cid=intent.client_order_id, filled=0.4, remaining=0.6, status="open")
        )
    )
    eng2._reconcile(intent2)
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)


def test_F_duplicate_exchange_trade_noop(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(
        symbol="BTC/IDR", side="buy", quote_amount=500_000, last_price=1_000_000
    )
    intent.normalized_base = 0.5
    eng.risk.try_reserve_notional(500_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    order = _exchange_order(
        cid=intent.client_order_id, filled=0.5, remaining=0.0, status="closed", amount=0.5
    )
    eng.client.find_order_by_client_id = MagicMock(return_value=OrderLookupResult.found(order))
    for _ in range(3):
        eng._reconcile(intent)
        intent = eng.intents.by_client_id(intent.client_order_id)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.5)
    assert eng.risk.pnl.today_notional() == pytest.approx(500_000)
