"""Tokocrypto LIVE adapter via CCXT (hardened). LIVE only."""

from __future__ import annotations

import logging
import time
from typing import Any

import ccxt  # type: ignore

from tko.core.credentials import TokocryptoCredentials
from tko.core.types import Balance, OHLCV, OrderResult, OrderType, Side, Ticker
from tko.exchange.constraints import MarketConstraints, extract_market_constraints
from tko.exchange.order_response import InvalidOrderResponse, validate_order_payload
from tko.execution.errors import ErrorCategory, classify_exception, is_ambiguous

logger = logging.getLogger(__name__)
DEFAULT_RECV_WINDOW = 5000
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_RATE_LIMIT_COOLDOWN_SEC = 60.0


class TokocryptoError(Exception):
    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.UNKNOWN_ERROR,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.ambiguous = ambiguous


class TokocryptoClient:
    def __init__(self, creds: TokocryptoCredentials) -> None:
        self._creds = creds
        self._client: Any = None
        self._constraints_cache: dict[str, MarketConstraints] = {}
        self._circuit_open = False
        self._circuit_reason: str | None = None
        self._rate_limit_until: float = 0.0
        self._timeout_ms: int = DEFAULT_TIMEOUT_MS

    @property
    def circuit_open(self) -> bool:
        if self._circuit_open:
            return True
        if self._rate_limit_until > time.time():
            return True
        return False

    @property
    def circuit_reason(self) -> str | None:
        if self._circuit_open:
            return self._circuit_reason
        if self._rate_limit_until > time.time():
            return f"rate_limit_cooldown until={self._rate_limit_until:.0f}"
        return None

    def open_circuit(self, reason: str) -> None:
        self._circuit_open = True
        self._circuit_reason = reason
        logger.critical("event=exchange_circuit_breaker_opened reason=%s", reason)

    def trip_rate_limit(self, cooldown_sec: float = DEFAULT_RATE_LIMIT_COOLDOWN_SEC) -> None:
        until = time.time() + max(1.0, float(cooldown_sec))
        self._rate_limit_until = max(self._rate_limit_until, until)
        logger.warning(
            "event=rate_limit_cooldown_armed cooldown_sec=%.1f until=%.0f",
            cooldown_sec,
            self._rate_limit_until,
        )

    def connect(self) -> None:
        if self._client is not None:
            return
        options: dict[str, Any] = {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
            "recvWindow": DEFAULT_RECV_WINDOW,
        }
        self._client = ccxt.tokocrypto(
            {
                "apiKey": self._creds.api_key.get_secret_value(),
                "secret": self._creds.api_secret.get_secret_value(),
                "enableRateLimit": True,
                "timeout": int(self._timeout_ms),
                "options": options,
            }
        )
        self._client.load_markets()
        self._constraints_cache.clear()
        for sym, m in (self._client.markets or {}).items():
            try:
                self._constraints_cache[sym] = extract_market_constraints(m)
            except Exception as exc:
                logger.warning("constraints parse failed for %s: %s", sym, exp)


