"""Stage 3 final gate: no duplicate POST, recon fail-closed, clientOrderId safety."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tko.core.config import Settings
from tko.core.types import OrderResult, OrderType, Side, Signal
from tko.exchange.tokocrypto import DEFAULT_TIMEOUT_MS, TokocryptoClient, TokocryptoError
from tko.execution.engine import ExecutionEngine
from tko.execution.intent import IntentStore, OrderIntentStatus
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState


def _ready_lifecycle() -> LifecycleGovernor:
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="test")
    return g


def test_recon_found_valid_confirms(tmp_path: Path):
    client = MagicMock()
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    client.find_order_by_client_id.return_value = {
        "id": "ex-99", "clientOrderId": intent.client_order_id,
        "filled": 0.01, "remaining": 0.0, "amount": 0.01, "average": 1000.0, "status": "closed",
    }
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.CONFIRMED
    assert intent.exchange_order_id == "ex-99"


def test_recon_unknown_status_stays_unknown(tmp_path: Path):
    client = MagicMock()
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    client.find_order_by_client_id.return_value = {
        "id": "ex-1", "clientOrderId": intent.client_order_id,
        "filled": 0.01, "average": 1000.0, "status": "unknown",
    }
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.UNKNOWN
