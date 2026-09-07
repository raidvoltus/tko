"""Tokocrypto REST client with proper order error semantics."""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .auth import headers, prepare_signed_params

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tokocrypto.com"
BASE_URL_MBX = "https://www.tokocrypto.site"  # symbolType=1 market data


class OrderResultStatus(str, Enum):
    ACK = "ACK"
    REJECTED = "REJECTED"          # 4xx
    RATE_LIMITED = "RATE_LIMITED"  # 429
    IP_BANNED = "IP_BANNED"        # 418
    UNKNOWN = "UNKNOWN"            # 5xx / timeout / network
    NETWORK_ERROR = "NETWORK_ERROR"


class RestClient:
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        base_url: str = BASE_URL,
        recv_window: int = 5000,
        timeout: float = 15.0,
        max_retries_on_get: int = 3,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.recv_window = recv_window
        self.timeout = timeout
        self.session = requests.Session()
        # Only retry safe methods. Never auto-retry POST orders.
        retry = Retry(
            total=max_retries_on_get,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "HEAD"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._last_request_time = 0.0
        self._min_interval = 0.05  # basic client-side throttle

    def _throttle(self) -> None:
        now = time.time()
        delta = now - self._last_request_time
        if delta < self._min_interval:
            time.sleep(self._min_interval - delta)
        self._last_request_time = time.time()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        signed: bool = False,
        is_order: bool = False,
    ) -> Tuple[OrderResultStatus, Dict[str, Any], Optional[requests.Response]]:
        """
        Unified request. For order endpoints returns explicit status.
        Never blind-retries POST order.
        """
        self._throttle()
        url = f"{self.base_url}{path}"
        params = dict(params or {})
        hdrs = headers(self.api_key) if self.api_key else {}

        if signed:
            if not self.api_secret:
                return OrderResultStatus.REJECTED, {"error": "missing api_secret"}, None
            params = prepare_signed_params(params, self.api_secret, self.recv_window)

        try:
            if method.upper() == "GET":
                resp = self.session.get(url, params=params, headers=hdrs, timeout=self.timeout)
            elif method.upper() == "POST":
                # form body
                resp = self.session.post(url, data=params, headers=hdrs, timeout=self.timeout)
            elif method.upper() == "DELETE":
                resp = self.session.delete(url, params=params, headers=hdrs, timeout=self.timeout)
            elif method.upper() == "PUT":
                resp = self.session.put(url, data=params, headers=hdrs, timeout=self.timeout)
            else:
                raise ValueError(f"Unsupported method {method}")
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.warning("Network error on %s %s: %s", method, path, e)
            return OrderResultStatus.UNKNOWN if is_order else OrderResultStatus.NETWORK_ERROR, {
                "error": str(e),
                "type": type(e).__name__,
            }, None
        except Exception as e:
            logger.exception("Unexpected error on %s %s", method, path)
            return OrderResultStatus.UNKNOWN if is_order else OrderResultStatus.NETWORK_ERROR, {
                "error": str(e),
            }, None

        status_code = resp.status_code
        try:
            body = resp.json() if resp.content else {}
        except Exception:
            body = {"raw": resp.text[:500]}

        if is_order:
            if 400 <= status_code < 500 and status_code != 429:
                return OrderResultStatus.REJECTED, body, resp
            if status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                body["_retry_after"] = retry_after
                return OrderResultStatus.RATE_LIMITED, body, resp
            if status_code == 418:
                return OrderResultStatus.IP_BANNED, body, resp
            if status_code >= 500:
                return OrderResultStatus.UNKNOWN, body, resp
            if status_code == 200:
                return OrderResultStatus.ACK, body, resp
            return OrderResultStatus.UNKNOWN, body, resp

        # non-order
        if status_code == 429:
            return OrderResultStatus.RATE_LIMITED, body, resp
        if status_code == 418:
            return OrderResultStatus.IP_BANNED, body, resp
        if status_code >= 400:
            return OrderResultStatus.REJECTED, body, resp
        return OrderResultStatus.ACK, body, resp

    # ---------- Public / market ----------
    def server_time(self) -> Dict:
        st, body, _ = self._request("GET", "/open/v1/common/time")
        return body

    def exchange_info(self) -> Dict:
        st, body, _ = self._request("GET", "/open/v1/common/symbols")
        return body

    def ticker(self, symbol: str) -> Dict:
        st, body, _ = self._request("GET", "/open/v1/market/ticker", {"symbol": symbol})
        return body

    def depth(self, symbol: str, limit: int = 100) -> Dict:
        # MBX style for type 1
        url = f"{BASE_URL_MBX}/api/v3/depth"
        # symbol for MBX often without underscore
        sym = symbol.replace("_", "")
        try:
            resp = self.session.get(url, params={"symbol": sym, "limit": limit}, timeout=self.timeout)
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    # ---------- Account / user ----------
    def account(self) -> Tuple[OrderResultStatus, Dict]:
        return self._request("GET", "/open/v1/account/spot", signed=True)[:2]

    def create_listen_token(self) -> Tuple[OrderResultStatus, Dict]:
        """POST /open/v1/user-listen-token (preferred over deprecated user-data-stream)."""
        return self._request("POST", "/open/v1/user-listen-token", signed=True)[:2]

    def create_listen_key(self) -> Tuple[OrderResultStatus, Dict]:
        """Legacy POST /open/v1/user-data-stream."""
        return self._request("POST", "/open/v1/user-data-stream", signed=True)[:2]

    def keepalive_listen_key(self, listen_key: str) -> Tuple[OrderResultStatus, Dict]:
        return self._request(
            "PUT", "/open/v1/user-data-stream", {"listenKey": listen_key}, signed=True
        )[:2]

    # ---------- Orders ----------
    def new_order(
        self,
        symbol: str,
        side: int,          # 0=BUY, 1=SELL (per Tokocrypto docs)
        order_type: int,    # 1=LIMIT, 2=MARKET, ...
        quantity: Optional[str] = None,
        price: Optional[str] = None,
        quote_order_qty: Optional[str] = None,
        time_in_force: Optional[int] = None,
        client_id: Optional[str] = None,
        self_trade_prevention_mode: Optional[int] = None,
    ) -> Tuple[OrderResultStatus, Dict]:
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
        }
        if quantity is not None:
            params["quantity"] = quantity
        if price is not None:
            params["price"] = price
        if quote_order_qty is not None:
            params["quoteOrderQty"] = quote_order_qty
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        if client_id is not None:
            params["clientId"] = client_id
        if self_trade_prevention_mode is not None:
            params["selfTradePreventionMode"] = self_trade_prevention_mode

        return self._request(
            "POST", "/open/v1/orders", params=params, signed=True, is_order=True
        )[:2]

    def query_order(
        self, order_id: Optional[int] = None, client_id: Optional[str] = None
    ) -> Tuple[OrderResultStatus, Dict]:
        params: Dict[str, Any] = {}
        if order_id is not None:
            params["orderId"] = order_id
        if client_id is not None:
            params["clientId"] = client_id
        return self._request(
            "GET", "/open/v1/orders/detail", params=params, signed=True
        )[:2]

    def cancel_order(
        self, order_id: Optional[int] = None, client_id: Optional[str] = None
    ) -> Tuple[OrderResultStatus, Dict]:
        params: Dict[str, Any] = {}
        if order_id is not None:
            params["orderId"] = order_id
        if client_id is not None:
            params["clientId"] = client_id
        return self._request(
            "POST", "/open/v1/orders/cancel", params=params, signed=True, is_order=True
        )[:2]

    def open_orders(self, symbol: str) -> Tuple[OrderResultStatus, Dict]:
        return self._request(
            "GET", "/open/v1/orders", {"symbol": symbol, "type": 1}, signed=True
        )[:2]
