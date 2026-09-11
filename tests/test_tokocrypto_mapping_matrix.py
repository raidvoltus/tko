"""Finding 7: Tokocrypto production mapping matrix (fail-closed)."""
from __future__ import annotations
import pytest
from tko.core.types import Side, OrderType
from tko.exchange.order_response import InvalidOrderResponse, validate_order_payload
from tko.execution.errors import ErrorCategory, classify_exception, is_ambiguous

def test_valid_closed_order():
    v = validate_order_payload({
        "id": "oid-1", "clientOrderId": "cid-1", "status": "closed",
        "filled": 0.01, "remaining": 0, "amount": 0.01, "average": 1000, "symbol": "BTC/IDR", "side": "buy",
    }, expected_client_order_id="cid-1")
    assert v.id == "oid-1"
    assert v.status == "closed"
    assert v.filled == 0.01

def test_missing_id_fails():
    with pytest.raises(InvalidOrderResponse):
        validate_order_payload({"clientOrderId": "c", "status": "closed", "filled": 0})

def test_nan_filled_fails():
    with pytest.raises(InvalidOrderResponse):
        validate_order_payload({"id": "1", "clientOrderId": "c", "filled": float("nan"), "status": "closed"})

def test_negative_filled_fails():
    with pytest.raises(InvalidOrderResponse):
        validate_order_payload({"id": "1", "clientOrderId": "c", "filled": -1, "status": "closed"})

def test_client_id_required():
    with pytest.raises(InvalidOrderResponse):
        validate_order_payload({"id": "1", "filled": 0, "status": "closed"}, expected_client_order_id="expected")

def test_side_and_order_type_enums():
    assert Side.BUY.value.lower() == "buy"
    assert Side.SELL.value.lower() == "sell"
    assert OrderType.MARKET.value.lower() in ("market", "MARKET".lower())

@pytest.mark.parametrize("msg,ambiguous", [
    ("timeout", True),
    ("HTTP 503 service unavailable", True),
    ("connection reset", True),
    ("insufficient balance", False),
])
def test_error_categories(msg, ambiguous):
    class E(Exception):
        pass
    cat = classify_exception(E(msg))
    assert is_ambiguous(cat) == ambiguous or (ambiguous and is_ambiguous(cat))
