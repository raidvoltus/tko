"""Stage 3 final gate: no duplicate POST, recon fail-closed, single authority, INV-18."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tko.core.config import Settings
from tko.core.types import OrderType, Side
from tko.exchange.order_response import InvalidOrderResponse, validate_order_payload
from tko.exchange.tokocrypto import DEFAULT_TIMEOUT_MS, TokocryptoClient, TokocryptoError
from tko.execution.engine import ExecutionEngine
from tko.execution.errors import ErrorCategory
from tko.execution.intent import BLOCKS_DUPLICATE, IntentStore, OrderIntentStatus
from tko.risk.engine import RiskDecision


def _ready_lifecycle():
    """READY LifecycleGovernor for LIVE submit tests (fail-closed engine requires it)."""
    from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState
    g = LifecycleGovernor()
    g.force(LifecycleState.READY, reason="test")
    return g


def test_blocks_include_governor_and_legacy():
    assert OrderIntentStatus.GOVERNOR_AUTONOMOUS in BLOCKS_DUPLICATE
    assert OrderIntentStatus.MANUAL_REVIEW in BLOCKS_DUPLICATE
    assert OrderIntentStatus.RETRY_ELIGIBLE in BLOCKS_DUPLICATE
    assert OrderIntentStatus.UNKNOWN in BLOCKS_DUPLICATE
    assert OrderIntentStatus.RECONCILIATION in BLOCKS_DUPLICATE


def test_a_timeout_unknown_no_second_post(tmp_path: Path):
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

    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 1
    assert eng.intents.has_blocking_intent("BTC/IDR", "buy") is True
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 1


def test_b_restart_unknown_blocks(tmp_path: Path):
    path = tmp_path / "order_intents.json"
    store = IntentStore(path)
    intent = store.create(symbol="ETH/IDR", side="buy", quote_amount=1)
    intent.status = OrderIntentStatus.UNKNOWN
    store.update(intent)

    client = MagicMock()
    client.circuit_open = False
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    eng.intents = IntentStore(path)
    eng.reconciler.intents = eng.intents
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    client.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    client.get_constraints.return_value = constraints
    assert eng.buy("ETH/IDR", "ETH", "IDR", dec, last_price=1000.0) is None
    assert client.create_order.call_count == 0


def test_c_recon_empty_id_not_confirmed(tmp_path: Path):
    client = MagicMock()
    client.circuit_open = False
    client.find_order_by_client_id.return_value = {"id": ""}
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status != OrderIntentStatus.CONFIRMED
    assert intent.status in (
        OrderIntentStatus.RECONCILIATION,
        OrderIntentStatus.GOVERNOR_AUTONOMOUS,
    )


def test_d_recon_nan_not_confirmed(tmp_path: Path):
    client = MagicMock()
    client.find_order_by_client_id.return_value = {"id": "123", "filled": float("nan")}
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status != OrderIntentStatus.CONFIRMED


def test_e_recon_negative_not_confirmed(tmp_path: Path):
    client = MagicMock()
    client.find_order_by_client_id.return_value = {"id": "123", "filled": -1}
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status != OrderIntentStatus.CONFIRMED


def test_f_recon_query_timeout_not_miss(tmp_path: Path):
    client = MagicMock()
    client.find_order_by_client_id.side_effect = TimeoutError("recon timeout")
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    intent.attempts = 0
    eng.intents.update(intent)
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.attempts == 0
    assert intent.status == OrderIntentStatus.RECONCILIATION
    assert eng.intents.has_blocking_intent("BTC/IDR", "buy")


def test_g_client_id_mismatch_not_confirmed(tmp_path: Path):
    client = MagicMock()
    client.find_order_by_client_id.return_value = {
        "id": "99",
        "filled": 0.1,
        "clientOrderId": "OTHER",
    }
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status != OrderIntentStatus.CONFIRMED


def test_h_concurrent_create_if_absent(tmp_path: Path):
    store = IntentStore(tmp_path / "i.json")
    results: list = []

    def worker():
        results.append(store.create_if_absent(symbol="BTC/IDR", side="buy", quote_amount=1))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([r for r in results if r is not None]) == 1


def test_i_429_storm_opens_circuit():
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr

    client = TokocryptoClient(
        TokocryptoCredentials(SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy"))
    )
    for _ in range(5):
        client.trip_rate_limit(cooldown_sec=60)
    assert client.circuit_open is True


def test_recon_misses_to_governor(tmp_path: Path):
    client = MagicMock()
    client.find_order_by_client_id.return_value = None
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    for _ in range(5):
        eng._reconcile(intent, max_misses=5)
        intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.GOVERNOR_AUTONOMOUS
    assert eng.intents.has_blocking_intent("BTC/IDR", "buy")


def test_recon_found_valid_confirms(tmp_path: Path):
    client = MagicMock()
    client.find_order_by_client_id.return_value = {
        "id": "ex-99",
        "filled": 0.01,
        "average": 1000.0,
    }
    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    eng.intents.update(intent)
    eng._reconcile(intent)
    intent = eng.intents.by_client_id(intent.client_order_id)
    assert intent.status == OrderIntentStatus.CONFIRMED
    assert intent.exchange_order_id == "ex-99"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"id": ""},
        {"id": None},
        {"id": "1", "filled": float("nan")},
        {"id": "1", "filled": float("inf")},
        {"id": "1", "filled": float("-inf")},
        {"id": "1", "filled": -1},
        {"id": "1", "average": float("nan")},
        {"id": "1", "average": float("inf")},
        {"id": "1", "average": -5},
    ],
)
def test_validate_order_payload_rejects(payload):
    with pytest.raises(InvalidOrderResponse):
        validate_order_payload(payload)


def test_validate_order_payload_accepts_valid():
    v = validate_order_payload({"id": "abc", "filled": 1.0, "average": 10.0})
    assert v.id == "abc"
    assert v.filled == 1.0


def test_timeout_constant_explicit():
    assert DEFAULT_TIMEOUT_MS >= 5000


def test_empty_order_id_on_create_rejected():
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr

    client = TokocryptoClient(
        TokocryptoCredentials(SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy"))
    )
    with pytest.raises(TokocryptoError) as ei:
        client._parse_order_result(
            {"id": ""}, "BTC/IDR", Side.BUY, OrderType.MARKET, 1.0, None, None, "cid-1"
        )
    assert ei.value.category == ErrorCategory.INVALID_RESPONSE


def test_legacy_manual_review_maps_to_governor(tmp_path: Path):
    import json

    path = tmp_path / "i.json"
    store = IntentStore(path)
    intent = store.create(symbol="X/IDR", side="buy")
    data = json.loads(path.read_text())
    data["intents"][0]["status"] = "MANUAL_REVIEW"
    path.write_text(json.dumps(data))
    store2 = IntentStore(path)
    loaded = store2.by_client_id(intent.client_order_id)
    assert loaded is not None
    assert loaded.status == OrderIntentStatus.GOVERNOR_AUTONOMOUS
    assert store2.has_blocking_intent("X/IDR", "buy")


def test_adapter_all_fetchers_fail_query_failed():
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr
    from tko.exchange.order_response import OrderLookupStatus

    client = TokocryptoClient(
        TokocryptoCredentials(SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy"))
    )
    mock_ccxt = MagicMock()
    mock_ccxt.fetch_open_orders.side_effect = TimeoutError("open timeout")
    mock_ccxt.fetch_closed_orders.side_effect = TimeoutError("closed timeout")
    mock_ccxt.fetch_orders.side_effect = TimeoutError("orders timeout")
    client._client = mock_ccxt

    result = client.find_order_by_client_id("BTC/IDR", "CID-1")
    assert result.status == OrderLookupStatus.QUERY_FAILED
    assert result.order is None
    assert result.error


def test_adapter_successful_empty_lists_not_found():
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr
    from tko.exchange.order_response import OrderLookupStatus

    client = TokocryptoClient(
        TokocryptoCredentials(SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy"))
    )
    mock_ccxt = MagicMock()
    mock_ccxt.fetch_open_orders.return_value = []
    mock_ccxt.fetch_closed_orders.return_value = []
    mock_ccxt.fetch_orders.return_value = []
    client._client = mock_ccxt

    result = client.find_order_by_client_id("BTC/IDR", "CID-1")
    assert result.status == OrderLookupStatus.NOT_FOUND


def test_adapter_found_returns_order():
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr
    from tko.exchange.order_response import OrderLookupStatus

    client = TokocryptoClient(
        TokocryptoCredentials(SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy"))
    )
    mock_ccxt = MagicMock()
    order = {"id": "99", "clientOrderId": "CID-1", "filled": 0.1}
    mock_ccxt.fetch_open_orders.return_value = [order]
    client._client = mock_ccxt

    result = client.find_order_by_client_id("BTC/IDR", "CID-1")
    assert result.status == OrderLookupStatus.FOUND
    assert result.order is not None
    assert result.order["id"] == "99"


def test_recon_adapter_query_failed_no_miss_no_governor(tmp_path: Path):
    from tko.core.credentials import TokocryptoCredentials
    from tko.core.types import SecretStr

    client = TokocryptoClient(
        TokocryptoCredentials(SecretStr("valid_api_key_xxxxx"), SecretStr("valid_api_secret_yyyy"))
    )
    mock_ccxt = MagicMock()
    mock_ccxt.fetch_open_orders.side_effect = TimeoutError("t")
    mock_ccxt.fetch_closed_orders.side_effect = TimeoutError("t")
    mock_ccxt.fetch_orders.side_effect = TimeoutError("t")
    client._client = mock_ccxt

    eng = ExecutionEngine(client, Settings(min_quote_balance=1), tmp_path, lifecycle=_ready_lifecycle())
    intent = eng.intents.create(symbol="BTC/IDR", side="buy", quote_amount=1000)
    intent.status = OrderIntentStatus.UNKNOWN
    intent.attempts = 0
    eng.intents.update(intent)

    for _ in range(5):
        eng._reconcile(intent, max_misses=5)
        intent = eng.intents.by_client_id(intent.client_order_id)

    assert intent.attempts == 0
    assert intent.status == OrderIntentStatus.RECONCILIATION
    assert intent.error_category == "RECON_QUERY_FAILED"
    assert intent.status != OrderIntentStatus.GOVERNOR_AUTONOMOUS
    assert eng.intents.has_blocking_intent("BTC/IDR", "buy")

    client_post = MagicMock()
    client_post.circuit_open = False
    client_post.create_order = MagicMock()
    eng.client = client_post
    eng.reconciler.client = client_post
    client_post.validate_symbol_ready.return_value = (True, "ok")
    constraints = MagicMock()
    constraints.validate_notional.return_value = (True, "ok")
    client_post.get_constraints.return_value = constraints
    dec = RiskDecision(True, "approved", size_quote=10000, size_base=0.01)
    assert eng.buy("BTC/IDR", "BTC", "IDR", dec, last_price=1000.0) is None
    assert client_post.create_order.call_count == 0
