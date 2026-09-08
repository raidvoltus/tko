"""Tokocrypto LIVE adapter via CCXT."""

from __future__ import annotations

import logging
import time
from typing import Any

import ccxt  # type: ignore

from tko.core.credentials import TokocryptoCredentials
from tko.core.types import Balance, OHLCV, OrderResult, OrderType, Side, Ticker

logger = logging.getLogger(__name__)


class TokocryptoError(Exception):
    pass


class TokocryptoClient:
    """LIVE-capable Tokocrypto client (spot)."""

    def __init__(self, creds: TokocryptoCredentials) -> None:
        self._creds = creds
        self._client: Any = None

    def connect(self) -> None:
        if self._client is not None:
            return
        options: dict[str, Any] = {
            "defaultType": "spot",
            "adjustForTimeDifference": True,
            "recvWindow": 10000,
        }
        self._client = ccxt.tokocrypto(
            {
                "apiKey": self._creds.api_key.get_secret_value(),
                "secret": self._creds.api_secret.get_secret_value(),
                "enableRateLimit": True,
                "options": options,
            }
        )
        self._client.load_markets()
        logger.info("Connected to Tokocrypto (%d markets)", len(self._client.markets))

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def fetch_balance(self) -> dict[str, Balance]:
        self.connect()
        raw = self._client.fetch_balance()
        out: dict[str, Balance] = {}
        for asset, info in (raw.get("total") or {}).items():
            total = float(info or 0)
            free = float((raw.get("free") or {}).get(asset) or 0)
            used = float((raw.get("used") or {}).get(asset) or 0)
            if total > 0 or free > 0 or used > 0:
                out[asset.upper()] = Balance(
                    asset=asset.upper(), free=free, used=used, total=total
                )
        return out

    def free_balance(self, asset: str) -> float:
        bal = self.fetch_balance()
        b = bal.get(asset.upper())
        return b.free if b else 0.0

    def fetch_ticker(self, symbol: str) -> Ticker:
        self.connect()
        t = self._client.fetch_ticker(symbol)
        return Ticker(
            symbol=symbol,
            last=float(t["last"] or 0),
            bid=float(t["bid"]) if t.get("bid") is not None else None,
            ask=float(t["ask"]) if t.get("ask") is not None else None,
            volume=float(t["baseVolume"]) if t.get("baseVolume") is not None else None,
            timestamp_ms=int(t["timestamp"]) if t.get("timestamp") else None,
        )

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "15m", limit: int = 100
    ) -> list[OHLCV]:
        self.connect()
        rows = self._client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return [
            OHLCV(
                timestamp_ms=int(r[0]),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in rows
        ]

    def resolve_symbol(self, base: str, quote: str) -> str | None:
        """Return CCXT unified symbol if market exists."""
        self.connect()
        candidates = [
            f"{base}/{quote}",
            f"{base}_{quote}",
            f"{base}{quote}",
        ]
        markets = self._client.markets or {}
        for c in candidates:
            if c in markets:
                return c
        base_u, quote_u = base.upper(), quote.upper()
        for m, info in markets.items():
            if not info.get("active", True):
                continue
            if info.get("base", "").upper() == base_u and info.get("quote", "").upper() == quote_u:
                return m
        return None

    def create_order(
        self,
        symbol: str,
        side: Side,
        amount: float,
        order_type: OrderType = OrderType.MARKET,
        price: float | None = None,
        client_order_id: str | None = None,
    ) -> OrderResult:
        self.connect()
        params: dict[str, Any] = {}
        if client_order_id:
            params["clientOrderId"] = client_order_id
        try:
            if order_type == OrderType.MARKET:
                raw = self._client.create_order(
                    symbol, "market", side.value, amount, None, params
                )
            else:
                if price is None:
                    raise TokocryptoError("limit order requires price")
                raw = self._client.create_order(
                    symbol, "limit", side.value, amount, price, params
                )
        except Exception as exc:
            raise TokocryptoError(f"create_order failed: {exc}") from exc

        return OrderResult(
            id=str(raw.get("id") or ""),
            symbol=symbol,
            side=side,
            type=order_type,
            amount=float(raw.get("amount") or amount),
            price=float(raw["price"]) if raw.get("price") is not None else price,
            status=str(raw.get("status") or "unknown"),
            filled=float(raw.get("filled") or 0),
            remaining=float(raw.get("remaining") or 0),
            average=float(raw["average"]) if raw.get("average") is not None else None,
            client_order_id=client_order_id,
        )

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self.connect()
        return list(self._client.fetch_open_orders(symbol) or [])

    def server_time_ms(self) -> int:
        self.connect()
        try:
            return int(self._client.fetch_time())
        except Exception:
            return int(time.time() * 1000)
