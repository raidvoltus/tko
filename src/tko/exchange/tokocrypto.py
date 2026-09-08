"""Tokocrypto LIVE adapter via CCXT (hardened). LIVE only."""

from __future__ import annotations

import logging
import time
from typing import Any

import ccxt  # type: ignore

from tko.core.credentials import TokocryptoCredentials
from tko.core.types import Balance, OHLCV, OrderResult, OrderType, Side, Ticker
from tko.exchange.constraints import MarketConstraints, extract_market_constraints
from tko.execution.errors import ErrorCategory, classify_exception, is_ambiguous

logger = logging.getLogger(__name__)
DEFAULT_RECV_WINDOW = 5000


class TokocryptoError(Exception):
    def __init__(self, message: str, *, category: ErrorCategory = ErrorCategory.UNKNOWN_ERROR, ambiguous: bool = False) -> None:
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

    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    def open_circuit(self, reason: str) -> None:
        self._circuit_open = True
        self._circuit_reason = reason
        logger.critical("event=exchange_circuit_breaker_opened reason=%s", reason)

    def connect(self) -> None:
        if self._client is not None:
            return
        options: dict[str, Any] = {"defaultType": "spot", "adjustForTimeDifference": True, "recvWindow": DEFAULT_RECV_WINDOW}
        self._client = ccxt.tokocrypto({
            "apiKey": self._creds.api_key.get_secret_value(),
            "secret": self._creds.api_secret.get_secret_value(),
            "enableRateLimit": True,
            "options": options,
        })
        self._client.load_markets()
        self._constraints_cache.clear()
        for sym, m in (self._client.markets or {}).items():
            try:
                self._constraints_cache[sym] = extract_market_constraints(m)
            except Exception as exc:
                logger.warning("constraints parse failed for %s: %s", sym, exc)
        logger.info(
            "event=market_constraints_loaded markets=%d constraints=%d recvWindow=%d mode=LIVE",
            len(self._client.markets or {}), len(self._constraints_cache), DEFAULT_RECV_WINDOW,
        )

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
                out[asset.upper()] = Balance(asset=asset.upper(), free=free, used=used, total=total)
        return out

    def free_balance(self, asset: str) -> float:
        bal = self.fetch_balance()
        b = bal.get(asset.upper())
        return b.free if b else 0.0

    def fetch_ticker(self, symbol: str) -> Ticker:
        self.connect()
        t = self._client.fetch_ticker(symbol)
        return Ticker(
            symbol=symbol, last=float(t["last"] or 0),
            bid=float(t["bid"]) if t.get("bid") is not None else None,
            ask=float(t["ask"]) if t.get("ask") is not None else None,
            volume=float(t["baseVolume"]) if t.get("baseVolume") is not None else None,
            timestamp_ms=int(t["timestamp"]) if t.get("timestamp") else None,
        )

    def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> list[OHLCV]:
        self.connect()
        rows = self._client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return [OHLCV(timestamp_ms=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]), volume=float(r[5])) for r in rows]

    def resolve_symbol(self, base: str, quote: str) -> str | None:
        self.connect()
        base_u, quote_u = base.upper(), quote.upper()
        candidates = [f"{base_u}/{quote_u}", f"{base_u}_{quote_u}", f"{base_u}{quote_u}"]
        markets = self._client.markets or {}
        for c in candidates:
            if c in markets and markets[c].get("active", True) is not False:
                logger.info("event=symbol_validated symbol=%s base=%s quote=%s", c, base_u, quote_u)
                return c
        for m, info in markets.items():
            if not info.get("active", True):
                continue
            if str(info.get("base", "")).upper() == base_u and str(info.get("quote", "")).upper() == quote_u:
                logger.info("event=symbol_validated symbol=%s base=%s quote=%s", m, base_u, quote_u)
                return m
        return None

    def get_constraints(self, symbol: str) -> MarketConstraints | None:
        self.connect()
        if symbol in self._constraints_cache:
            return self._constraints_cache[symbol]
        markets = self._client.markets or {}
        if symbol not in markets:
            return None
        c = extract_market_constraints(markets[symbol])
        self._constraints_cache[symbol] = c
        return c

    def supports_quote_order_qty(self) -> bool:
        return True

    def create_order(
        self, symbol: str, side: Side, amount: float,
        order_type: OrderType = OrderType.MARKET, price: float | None = None,
        client_order_id: str | None = None, *, quote_amount: float | None = None,
    ) -> OrderResult:
        self.connect()
        if self._circuit_open:
            raise TokocryptoError(f"circuit breaker open: {self._circuit_reason}", category=ErrorCategory.CIRCUIT_BREAKER)
        params: dict[str, Any] = {}
        if client_order_id:
            params["clientOrderId"] = client_order_id
            params["clientId"] = client_order_id
        try:
            if order_type == OrderType.MARKET and side == Side.BUY and quote_amount is not None:
                if not self.supports_quote_order_qty():
                    raise TokocryptoError("quoteOrderQty path not supported — fail closed for LIVE BUY", category=ErrorCategory.INVALID_REQUEST)
                params["quoteOrderQty"] = quote_amount
                raw = self._client.create_order(symbol, "market", side.value, quote_amount, None, params)
            elif order_type == OrderType.MARKET:
                raw = self._client.create_order(symbol, "market", side.value, amount, None, params)
            else:
                if price is None:
                    raise TokocryptoError("limit order requires price", category=ErrorCategory.INVALID_REQUEST)
                raw = self._client.create_order(symbol, "limit", side.value, amount, price, params)
        except TokocryptoError:
            raise
        except Exception as exc:
            cat = classify_exception(exc)
            if cat == ErrorCategory.CIRCUIT_BREAKER:
                self.open_circuit(str(exc)[:200])
            if cat == ErrorCategory.RATE_LIMIT:
                logger.warning("event=rate_limit_detected detail=%s", type(exc).__name__)
            raise TokocryptoError(f"create_order failed: {exc}", category=cat, ambiguous=is_ambiguous(cat)) from exc
        return OrderResult(
            id=str(raw.get("id") or ""), symbol=symbol, side=side, type=order_type,
            amount=float(raw.get("amount") or amount or quote_amount or 0),
            price=float(raw["price"]) if raw.get("price") is not None else price,
            status=str(raw.get("status") or "unknown"),
            filled=float(raw.get("filled") or 0), remaining=float(raw.get("remaining") or 0),
            average=float(raw["average"]) if raw.get("average") is not None else None,
            client_order_id=client_order_id,
        )

    def find_order_by_client_id(self, symbol: str, client_order_id: str, *, lookback_limit: int = 50) -> dict[str, Any] | None:
        self.connect()
        for fetcher_name in ("fetch_open_orders", "fetch_closed_orders", "fetch_orders"):
            if not hasattr(self._client, fetcher_name):
                continue
            try:
                method = getattr(self._client, fetcher_name)
                kwargs = {"limit": lookback_limit} if fetcher_name != "fetch_open_orders" else {}
                orders = method(symbol, **kwargs) if fetcher_name != "fetch_open_orders" else method(symbol)
                for o in orders or []:
                    if self._match_client_id(o, client_order_id):
                        return o
            except Exception as exc:
                logger.warning("%s during recon: %s", fetcher_name, exc)
        return None

    @staticmethod
    def _match_client_id(order: dict[str, Any], client_order_id: str) -> bool:
        if not client_order_id:
            return False
        for key in ("clientOrderId", "clientOrderID", "clientId", "client_id"):
            val = order.get(key)
            if val is not None and str(val) == client_order_id:
                return True
        info = order.get("info") or {}
        if isinstance(info, dict):
            for key in ("clientId", "clientOrderId", "client_id"):
                val = info.get(key)
                if val is not None and str(val) == client_order_id:
                    return True
        return False

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        self.connect()
        return list(self._client.fetch_open_orders(symbol) or [])

    def server_time_ms(self) -> int:
        self.connect()
        try:
            return int(self._client.fetch_time())
        except Exception:
            return int(time.time() * 1000)

    def validate_symbol_ready(self, symbol: str) -> tuple[bool, str]:
        c = self.get_constraints(symbol)
        if c is None:
            return False, "market metadata missing"
        if not c.active:
            return False, "market not active"
        if c.get_step_size() is None and c.amount_precision is None:
            return False, "no amount step/precision available"
        return True, "ok"
