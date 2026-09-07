"""Exchange rule validation & quantity/price rounding (PRICE_FILTER, LOT_SIZE, etc.)."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SymbolFilters:
    symbol: str
    base_asset: str = ""
    quote_asset: str = ""
    price_tick: Decimal = Decimal("0.01")
    min_price: Decimal = Decimal("0")
    max_price: Decimal = Decimal("999999999")
    qty_step: Decimal = Decimal("0.0001")
    min_qty: Decimal = Decimal("0.0001")
    max_qty: Decimal = Decimal("999999")
    min_notional: Decimal = Decimal("10")
    market_qty_step: Optional[Decimal] = None
    market_min_qty: Optional[Decimal] = None
    market_max_qty: Optional[Decimal] = None
    max_num_orders: int = 200
    max_num_algo_orders: int = 5
    percent_price_multiplier_up: Optional[Decimal] = None
    percent_price_multiplier_down: Optional[Decimal] = None
    raw: Dict = field(default_factory=dict)


def _d(v: Any) -> Decimal:
    return Decimal(str(v))


def parse_filters(symbol_info: Dict) -> SymbolFilters:
    """Parse Tokocrypto / open/v1/common/symbols style filters."""
    symbol = symbol_info.get("symbol", "")
    filters = {f["filterType"]: f for f in symbol_info.get("filters", [])}
    sf = SymbolFilters(
        symbol=symbol,
        base_asset=symbol_info.get("baseAsset", ""),
        quote_asset=symbol_info.get("quoteAsset", ""),
        raw=symbol_info,
    )
    if "PRICE_FILTER" in filters:
        pf = filters["PRICE_FILTER"]
        sf.price_tick = _d(pf.get("tickSize", "0.01"))
        sf.min_price = _d(pf.get("minPrice", "0"))
        sf.max_price = _d(pf.get("maxPrice", "999999999"))
    if "LOT_SIZE" in filters:
        ls = filters["LOT_SIZE"]
        sf.qty_step = _d(ls.get("stepSize", "0.0001"))
        sf.min_qty = _d(ls.get("minQty", "0.0001"))
        sf.max_qty = _d(ls.get("maxQty", "999999"))
    if "MARKET_LOT_SIZE" in filters:
        mls = filters["MARKET_LOT_SIZE"]
        sf.market_qty_step = _d(mls.get("stepSize", sf.qty_step))
        sf.market_min_qty = _d(mls.get("minQty", sf.min_qty))
        sf.market_max_qty = _d(mls.get("maxQty", sf.max_qty))
    if "MIN_NOTIONAL" in filters or "NOTIONAL" in filters:
        nf = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL")
        sf.min_notional = _d(nf.get("minNotional", nf.get("notional", "10")))
    if "PERCENT_PRICE" in filters:
        pp = filters["PERCENT_PRICE"]
        sf.percent_price_multiplier_up = _d(pp.get("multiplierUp", "1.1"))
        sf.percent_price_multiplier_down = _d(pp.get("multiplierDown", "0.9"))
    return sf


def round_step(value: Decimal, step: Decimal, mode: str = "down") -> Decimal:
    if step <= 0:
        return value
    # quantize to step
    n = (value / step).to_integral_value(
        rounding=ROUND_DOWN if mode == "down" else ROUND_HALF_UP
    )
    return n * step


def normalize_order(
    filters: SymbolFilters,
    side: int,
    order_type: int,
    quantity: Optional[str] = None,
    price: Optional[str] = None,
    quote_order_qty: Optional[str] = None,
    reference_price: Optional[Decimal] = None,
) -> Tuple[bool, Dict[str, str], str]:
    """
    Apply LOT_SIZE / PRICE_FILTER / NOTIONAL.
    Returns (ok, normalized_params, error_message).
    """
    out: Dict[str, str] = {}
    qty = _d(quantity) if quantity else None
    px = _d(price) if price else None
    is_market = order_type == 2

    step = filters.market_qty_step if is_market and filters.market_qty_step else filters.qty_step
    min_q = filters.market_min_qty if is_market and filters.market_min_qty else filters.min_qty
    max_q = filters.market_max_qty if is_market and filters.market_max_qty else filters.max_qty

    if qty is not None:
        qty = round_step(qty, step, "down")
        if qty < min_q:
            return False, {}, f"qty {qty} < minQty {min_q}"
        if qty > max_q:
            return False, {}, f"qty {qty} > maxQty {max_q}"
        out["quantity"] = format(qty, "f")

    if px is not None and not is_market:
        px = round_step(px, filters.price_tick, "down")
        if px < filters.min_price:
            return False, {}, f"price {px} < minPrice {filters.min_price}"
        if px > filters.max_price:
            return False, {}, f"price {px} > maxPrice {filters.max_price}"
        if reference_price and filters.percent_price_multiplier_up:
            up = reference_price * filters.percent_price_multiplier_up
            down = reference_price * (filters.percent_price_multiplier_down or Decimal("0"))
            if px > up or px < down:
                return False, {}, f"price {px} outside PERCENT_PRICE band"
        out["price"] = format(px, "f")

    # notional check
    if qty is not None and px is not None:
        notional = qty * px
        if notional < filters.min_notional:
            return False, {}, f"notional {notional} < minNotional {filters.min_notional}"
    elif quote_order_qty is not None:
        qoq = _d(quote_order_qty)
        if qoq < filters.min_notional:
            return False, {}, f"quoteOrderQty {qoq} < minNotional"
        out["quoteOrderQty"] = format(qoq, "f")

    return True, out, ""
