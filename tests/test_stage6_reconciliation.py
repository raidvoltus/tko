"""Stage 6 one-shot: order + fill + position + PnL + restart reconciliation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.core.types import Side
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
    eng.reconciler = Reconciler(
        client=eng.client, intents=eng.intents, positions=eng.positions_store, max_unknown_checks=5
    )
    return eng


def _ex(*, cid: str, filled: float, remaining: float, status: str, amount: float = 1.0) -> dict:
    return {
        "id": "ex-1",
        "clientOrderId": cid,
        "symbol": "BTC/IDR",
        "side": "buy",
        "status": status,
        "filled": filled,
        "remaining": remaining,
        "amount": amount,
        "average": 1_000_000.0,
        "price": 1_000_000.0,
    }


def test_unknown_recon_accounts_once_no_duplicate_order(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1_000_000, last_price=1e6)
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1_000_000, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    assert eng.intents.create_if_absent(symbol="BTC/IDR", side="buy", quote_amount=1e6, last_price=1e6) is None
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(_ex(cid=intent.client_order_id, filled=1.0, remaining=0.0, status="closed"))
    )
    eng._reconcile(intent)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    eng._reconcile(intent)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(1.0)
    assert eng.risk.pnl.today_notional() == pytest.approx(1_000_000)


def test_query_failed_stays_blocking(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1e5, last_price=1e6)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(return_value=OrderLookupResult.query_failed("5xx"))
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.error_category == "RECON_QUERY_FAILED"
    assert intent.status in BLOCKS_DUPLICATE


def test_canceled_zero_fill_rejected_releases_reservation(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1e6, last_price=1e6)
    eng.risk.try_reserve_notional(1e6, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(_ex(cid=intent.client_order_id, filled=0.0, remaining=1.0, status="canceled"))
    )
    eng._reconcile(intent)
    assert eng.intents.by_client_id(intent.client_order_id).status == OrderIntentStatus.REJECTED
    assert eng.risk.reserved_notional() == pytest.approx(0.0)
    assert eng.risk.pnl.today_notional() == pytest.approx(0.0)


def test_partial_then_cumulative_delta(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1e6, last_price=1e6)
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1e6, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(_ex(cid=intent.client_order_id, filled=0.4, remaining=0.6, status="open"))
    )
    eng._reconcile(intent)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(_ex(cid=intent.client_order_id, filled=0.7, remaining=0.3, status="open"))
    )
    eng._reconcile(intent)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.7)
    assert eng.risk.pnl.today_notional() == pytest.approx(700_000)


def test_duplicate_recon_noop(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=5e5, last_price=1e6)
    intent.normalized_base = 0.5
    eng.risk.try_reserve_notional(5e5, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    order = _ex(cid=intent.client_order_id, filled=0.5, remaining=0.0, status="closed", amount=0.5)
    eng.client.find_order_by_client_id = MagicMock(return_value=OrderLookupResult.found(order))
    for _ in range(3):
        eng._reconcile(intent)
        intent = eng.intents.by_client_id(intent.client_order_id)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.5)
    assert eng.risk.pnl.today_notional() == pytest.approx(500_000)


def test_position_discrepancy_detected_and_aligned_no_trade(tmp_path: Path):
    eng = _build(tmp_path)
    eng.positions_store.upsert(
        symbol="BTC/IDR", base="BTC", quote="IDR", amount=1.0, entry_price=1e6
    )
    free_map = {"BTC": 0.6, "IDR": 1e9}
    disc = eng.positions_store.detect_discrepancies(free_map)
    assert len(disc) == 1
    assert disc[0].delta == pytest.approx(0.4)

    result = eng.reconciler.reconcile_all(free_map)
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.6)
    assert result.safe_to_trade is True
    eng.client.create_order.assert_not_called()


def test_position_match_no_discrepancy(tmp_path: Path):
    eng = _build(tmp_path)
    eng.positions_store.upsert(
        symbol="BTC/IDR", base="BTC", quote="IDR", amount=0.5, entry_price=1e6
    )
    free_map = {"BTC": 0.5, "IDR": 1e9}
    assert eng.positions_store.detect_discrepancies(free_map) == []
    result = eng.reconciler.reconcile_all(free_map)
    assert result.safe_to_trade is True
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.5)


def test_restart_after_partial_recon_deterministic(tmp_path: Path):
    eng = _build(tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1e6, last_price=1e6)
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1e6, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(_ex(cid=intent.client_order_id, filled=0.4, remaining=0.6, status="open"))
    )
    eng._reconcile(intent)

    eng2 = _build(tmp_path)
    eng2._replay_unapplied_fills()
    eng2.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(_ex(cid=intent.client_order_id, filled=0.4, remaining=0.6, status="open"))
    )
    eng2._reconcile(eng2.intents.by_client_id(intent.client_order_id))
    assert eng2.positions_store.get("BTC/IDR").amount == pytest.approx(0.4)
    assert eng2.risk.pnl.today_notional() == pytest.approx(400_000)


def test_recon_all_with_on_confirmed_accounts_fill(tmp_path: Path):
    """Startup path: reconcile_all MUST apply fills when on_confirmed is wired."""
    eng = _build(tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1e6, last_price=1e6)
    intent.normalized_base = 1.0
    eng.risk.try_reserve_notional(1e6, reservation_id=intent.client_order_id)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng.client.find_order_by_client_id = MagicMock(
        return_value=OrderLookupResult.found(
            _ex(cid=intent.client_order_id, filled=0.6, remaining=0.4, status="open")
        )
    )

    def _on_confirmed(i):
        side = Side.BUY if i.side == "buy" else Side.SELL
        synthetic = eng._result_from_intent(i, side)
        is_partial = i.status == OrderIntentStatus.PARTIALLY_FILLED
        eng._on_fill_confirmed(
            i, side, synthetic, base="BTC", quote="IDR",
            partial=is_partial, remaining=float(synthetic.remaining or 0.0),
        )

    eng.reconciler.reconcile_all(None, on_confirmed=_on_confirmed)
    assert eng.positions_store.get("BTC/IDR") is not None
    assert eng.positions_store.get("BTC/IDR").amount == pytest.approx(0.6)
    assert eng.risk.pnl.today_notional() == pytest.approx(600_000)
