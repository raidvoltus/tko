"""Production hardening F1–F6 + corruption gates."""
from __future__ import annotations
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from tko.core.config import Settings
from tko.exchange.order_response import InvalidOrderResponse, validate_order_payload, OrderLookupResult
from tko.execution.intent import IntentStore, OrderIntentStatus
from tko.reconciliation.reconciler import Reconciler
from tko.risk.engine import RiskEngine
from tko.risk.market_data import validate_market_data_freshness
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore

def S(**kw):
    b = dict(live_mode=True, max_order_notional=5e6, max_daily_notional=1e7, max_position_pct=100,
             min_quote_balance=1, max_open_positions=10, max_daily_loss_pct=5.0,
             daily_equity_baseline=1e6, risk_timezone="UTC", market_data_max_age_sec=60.0)
    b.update(kw)
    return Settings(**b)

@pytest.mark.parametrize("status", [None, "", "99", "weird", "FUTURE"])
def test_F1_unknown_not_confirmed(tmp_path, status):
    intents = IntentStore(tmp_path / "i.json")
    intent = intents.create(symbol="BTC/IDR", side="buy", quote_amount=1e6)
    intent.status = OrderIntentStatus.UNKNOWN
    intents.update(intent)
    client = MagicMock()
    client.find_order_by_client_id = MagicMock(return_value=OrderLookupResult.found({
        "id": "1", "clientOrderId": intent.client_order_id, "status": status,
        "filled": 1, "remaining": 0, "amount": 1, "average": 1,
    }))
    assert Reconciler(client=client, intents=intents).reconcile_intent(intent).status != OrderIntentStatus.CONFIRMED

def test_F1_closed_confirmed(tmp_path):
    intents = IntentStore(tmp_path / "i.json")
    intent = intents.create(symbol="BTC/IDR", side="buy", quote_amount=1e6)
    intent.status = OrderIntentStatus.UNKNOWN
    intents.update(intent)
    client = MagicMock()
    client.find_order_by_client_id = MagicMock(return_value=OrderLookupResult.found({
        "id": "1", "clientOrderId": intent.client_order_id, "status": "closed",
        "filled": 1, "remaining": 0, "amount": 1, "average": 1,
    }))
    assert Reconciler(client=client, intents=intents).reconcile_intent(intent).status == OrderIntentStatus.CONFIRMED

def test_F2_client_id():
    validate_order_payload({"id": "1", "clientOrderId": "C", "filled": 0, "remaining": 0, "status": "closed"}, expected_client_order_id="C")
    with pytest.raises(InvalidOrderResponse):
        validate_order_payload({"id": "1", "filled": 0, "remaining": 0, "status": "closed"}, expected_client_order_id="C")
    with pytest.raises(InvalidOrderResponse):
        validate_order_payload({"id": "1", "clientOrderId": "X", "filled": 0, "remaining": 0, "status": "closed"}, expected_client_order_id="C")

def test_F3_intent_corrupt(tmp_path):
    (tmp_path / "i.json").write_text("{x")
    s = IntentStore(tmp_path / "i.json")
    assert s.corrupted
    assert s.create_if_absent(symbol="BTC/IDR", side="buy") is None

def test_F4_position_corrupt(tmp_path):
    (tmp_path / "p.json").write_text("{x")
    assert PositionStore(tmp_path / "p.json").corrupted

def test_F5_baseline_restart(tmp_path):
    e1 = RiskEngine(S(), tmp_path, DailyPnLTracker(tmp_path / "p.jsonl", timezone_name="UTC"))
    assert e1.equity_baseline() == pytest.approx(1e6)
    e2 = RiskEngine(S(daily_equity_baseline=5e5), tmp_path, DailyPnLTracker(tmp_path / "p.jsonl", timezone_name="UTC"))
    assert e2.equity_baseline() == pytest.approx(1e6)

def test_F6_freshness():
    assert validate_market_data_freshness(timestamp=time.time(), max_age_sec=60).ok
    assert not validate_market_data_freshness(timestamp=time.time() - 120, max_age_sec=60).ok
    assert not validate_market_data_freshness(timestamp=None, max_age_sec=60).ok
    assert not validate_market_data_freshness(timestamp=float("nan"), max_age_sec=60).ok
    assert not validate_market_data_freshness(timestamp=float("inf"), max_age_sec=60).ok
    assert not validate_market_data_freshness(timestamp=time.time() + 60, max_age_sec=60).ok
    assert validate_market_data_freshness(timestamp=None, max_age_sec=0).ok

def test_F6_entry_stale_blocks(tmp_path):
    eng = RiskEngine(S(), tmp_path, DailyPnLTracker(tmp_path / "p.jsonl", timezone_name="UTC"))
    from tko.core.types import Signal
    d = eng.evaluate_entry(symbol="BTC/IDR", quote_free=1e9, last_price=1e6, signal=Signal.BUY, market_data_ts=time.time() - 999)
    assert not d.approved
