"""Stage 3 regression: no duplicate POST after UNKNOWN, response fail-closed, 429 cooldown."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.core.types import OrderType, Side
from tko.exchange.tokocrypto import DEFAULT_TIMEOUT_MS, TokocryptoClient, TokocryptoError
from tko.execution.engine import ExecutionEngine
from tko.execution.errors import ErrorCategory
from tko.execution.intent import BLOCKS_DUPLICATE, IntentStore, OrderIntentStatus
from tko.risk.engine import RiskDecision


def test_blocks_duplicate_includes_manual_and_retry():
    assert OrderIntentStatus.MANUAL_REVIEW in BLOCKS_DUPLICATE
    assert OrderIntentStatus.RETRY_ELIGIBLE in BLOCKS_DUPLICATE
    assert OrderIntentStatus.UNKNOWN in BLOCKS_DUPLICATE
    assert OrderIntentStatus.RECONCILIATION in BLOCKS_DUPLICATE


def test_timeout_unknown_stays_blocking_no_second_post(tmp_path: Path):
    client = MagicMock()
    client.circuit_open = False
    client.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    constraints.normalize_quantity.side_effect = lambda q, market_order=False: q
    constraints.validate_quantity.return_value = (True, "ok")
    client.get_constraints.return_value = constraints
    client.create_order.side_effect = TokocryptoError(
        "timeout", category=ErrorCategory.TIMEOUT, ambiguous=True
    )
    client.find_order_by_client_id.return_value = None

    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path)
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    result = eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0)
    assert result is None
    assert client.create_order.call_count == 1

    unresolved = eng.intents.unresolved_unknown()
    assert unresolved
    intent = unresolved[0]
    assert intent.status in (
        OrderIntentStatus.UNKNOWN,
        OrderIntentStatus.RECONCILIATION,
        OrderIntentStatus.MANUAL_REVIEW,
    )
    assert intent.status != OrderIntentStatus.RETRY_ELIGIBLE
    assert eng.intents.has_blocking_intent("BTC/IDR", "buy") is True

    result2 = eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0)
    assert result2 is None
    assert client.create_order.call_count == 1


def test_reconcile_misses_escalate_to_manual_review(tmp_path: Path):
    client = MagicMock()
    client.circuit_open = False
    client.find_order_by_client_id.return_value = None
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)

    for _ in range(5):
        eng._reconcile(intent, max_misses=5)
        intent = eng.intents.by_client_id(intent.client_order_id)

    assert intent.status == OrderIntentStatus.MANUAL_REVIEW
    assert eng.intents.has_blocking_intent("BTC/IDR", "buy") is True


def test_reconcile_found_confirms(tmp_path: Path):
    client = MagicMock()
    client.circuit_open = False
    client.find_order_by_client_id.return_value = {
        "id": "ex-99",
        "filled": 0.01,
        "average": 1000.0,
    }
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path)
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.CONFIRMED
    assert intent.exchange_order_id == "ex-99"


def test_empty_order_id_rejected():
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr

    client = TokocryptoClient(
        TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
    )
    with pytest.raises(TokocryptoError) as ei:
        client._parse_order_result(
            {"id": "", "filled": 1},
            symbol="BTC/IDR",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            amount=1.0,
            quote_amount=None,
            price=None,
            client_order_id="cid-1",
        )
    assert ei.value.category == ErrorCategory.INVALID_RESPONSE


def test_nan_filled_rejected():
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr

    client = TokocryptoClient(
        TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
    )
    with pytest.raises(TokocryptoError) as ei:
        client._parse_order_result(
            {"id": "1", "filled": float("nan")},
            symbol="BTC/IDR",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            amount=1.0,
            quote_amount=None,
            price=None,
            client_order_id="cid-1",
        )
    assert ei.value.category == ErrorCategory.INVALID_RESPONSE


def test_timeout_constant_explicit():
    assert DEFAULT_TIMEOUT_MS >= 5000


def test_rate_limit_trips_circuit_open():
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr

    client = TokocryptoClient(
        TokocryptoCredentials(
            SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy")
        )
    )
    assert client.circuit_open is False
    client.trip_rate_limit(cooldown_sec=30)
    assert client.circuit_open is True


def test_restart_loads_unknown_still_blocking(tmp_path: Path):
    path = tmp_path / "intents.json"
    store = IntentStore(path)
    intent = store.create(symbol="ETH/IDR", side="buy", quote_amount=1)
    intent.status = OrderIntentStatus.UNKNOWN
    store.update(intent)

    store2 = IntentStore(path)
    assert store2.has_blocking_intent("ETH/IDR", "buy") is True
    loaded = store2.by_client_id(intent.client_order_id)
    assert loaded is not None
    assert loaded.status == OrderIntentStatus.UNKNOWN
