"""Shared types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType

ExchangeId = NewType("ExchangeId", str)
AccountId = NewType("AccountId", str)


class SecretStr:
    """String that never appears in repr/str."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("SecretStr requires str")
        self._value = value

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretStr('***')"

    def __str__(self) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretStr):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class Balance:
    asset: str
    free: float
    used: float
    total: float


@dataclass(frozen=True, slots=True)
class Ticker:
    symbol: str
    last: float
    bid: float | None
    ask: float | None
    volume: float | None
    timestamp_ms: int | None


@dataclass(frozen=True, slots=True)
class OHLCV:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class OrderResult:
    id: str
    symbol: str
    side: Side
    type: OrderType
    amount: float
    price: float | None
    status: str
    filled: float
    remaining: float
    average: float | None
    client_order_id: str | None = None
