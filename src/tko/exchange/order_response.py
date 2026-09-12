"""Canonical fail-closed validation for exchange order payloads (POST + RECON)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any


class InvalidOrderResponse(Exception):
    """Exchange order payload failed validation — never treat as success."""


@dataclass(frozen=True, slots=True)
class ValidatedOrder:
    id: str
    filled: float
    remaining: float
    amount: float
    average: float | None
    price: float | None
    status: str
    client_order_id: str | None
    symbol: str | None
    side: str | None


def _finite(name: str, val: Any, *, required: bool = False, default: float | None = None) -> float | None:
    if val is None:
        if required:
            raise InvalidOrderResponse(f"missing {name}")
        return default
    try:
        f = float(val)
    except (TypeError, ValueError) as exc:
        raise InvalidOrderResponse(f"invalid {name}") from exc
    if not math.isfinite(f):
        raise InvalidOrderResponse(f"non-finite {name}")
    if f < 0:
        raise InvalidOrderResponse(f"negative {name}")
    return f


def validate_order_payload(
    raw: Any,
    *,
    expected_client_order_id: str | None = None,
    require_client_id_match: bool = False,
    default_amount: float | None = None,
) -> ValidatedOrder:
    """Fail-closed validation for create_order and reconciliation payloads."""
    if not isinstance(raw, dict):
        raise InvalidOrderResponse("order payload is not a dict")

    oid = raw.get("id")
    if oid is None or str(oid).strip() == "":
        raise InvalidOrderResponse("missing or empty order id")

    filled = _finite("filled", raw.get("filled"), default=0.0) or 0.0
    remaining = _finite("remaining", raw.get("remaining"), default=0.0) or 0.0
    amount = _finite("amount", raw.get("amount"), default=default_amount if default_amount is not None else 0.0) or 0.0
    average = _finite("average", raw.get("average"), default=None)
    price = _finite("price", raw.get("price"), default=None)

    resp_cid = raw.get("clientOrderId") or raw.get("clientOrderID") or raw.get("clientId")
    if resp_cid is not None:
        resp_cid = str(resp_cid)
    if expected_client_order_id and resp_cid is not None and resp_cid != str(expected_client_order_id):
        raise InvalidOrderResponse(
            f"clientOrderId mismatch: expected={expected_client_order_id} got={resp_cid}"
        )

    symbol = raw.get("symbol")
    side = raw.get("side")
    return ValidatedOrder(
        id=str(oid).strip(),
        filled=float(filled),
        remaining=float(remaining),
        amount=float(amount),
        average=average,
        price=price,
        status=str(raw.get("status") or "unknown"),
        client_order_id=resp_cid if resp_cid is not None else (expected_client_order_id or None),
        symbol=str(symbol) if symbol is not None else None,
        side=str(side).lower() if side is not None else None,
    )


class OrderLookupStatus(str, Enum):
    """Explicit recon lookup outcome — never collapse QUERY_FAILED into NOT_FOUND."""

    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    QUERY_FAILED = "QUERY_FAILED"


class OrderLookupResult:
    """Typed result for find_order_by_client_id (INV-18)."""

    __slots__ = ("error", "order", "status")

    def __init__(
        self,
        status: OrderLookupStatus,
        order: dict | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.order = order
        self.error = error

    @classmethod
    def found(cls, order: dict) -> OrderLookupResult:
        return cls(OrderLookupStatus.FOUND, order=order)

    @classmethod
    def not_found(cls) -> OrderLookupResult:
        return cls(OrderLookupStatus.NOT_FOUND)

    @classmethod
    def query_failed(cls, error: str) -> OrderLookupResult:
        return cls(OrderLookupStatus.QUERY_FAILED, error=error)
