"""Market constraints from exchange metadata (LOT_SIZE, NOTIONAL, precision)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _precision_fields(value: Any) -> tuple[int | None, Decimal | None]:
    """CCXT precision may be decimal-places (int) OR tick/step size (float/str).

    Returns (decimal_places, step_or_tick). Exactly one side is usually set.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, None
    if isinstance(value, int):
        return value, None
    if isinstance(value, float):
        d = _to_decimal(value)
        if d is None:
            return None, None
        if d == d.to_integral_value() and Decimal(0) <= d <= Decimal(18):
            return int(d), None
        return None, d
    s = str(value).strip()
    if not s:
        return None, None
    if s.isdigit() or (s[0] == "-" and s[1:].isdigit()):
        return int(s), None
    d = _to_decimal(s)
    if d is None:
        return None, None
    if d == d.to_integral_value() and Decimal(0) <= d <= Decimal(18):
        return int(d), None
    return None, d


@dataclass(frozen=True, slots=True)
class MarketConstraints:
    symbol: str
    base: str
    quote: str
    active: bool
    amount_min: Decimal | None
    amount_max: Decimal | None
    amount_step: Decimal | None
    cost_min: Decimal | None
    cost_max: Decimal | None
    price_tick: Decimal | None
    amount_precision: int | None
    price_precision: int | None
    market_amount_min: Decimal | None
    market_amount_max: Decimal | None
    market_amount_step: Decimal | None
    raw_info: dict[str, Any] | None = None

    def get_step_size(self, *, market_order: bool = True) -> Decimal | None:
        if market_order and self.market_amount_step is not None:
            return self.market_amount_step
        return self.amount_step

    def get_min_qty(self, *, market_order: bool = True) -> Decimal | None:
        if market_order and self.market_amount_min is not None:
            return self.market_amount_min
        return self.amount_min

    def get_max_qty(self, *, market_order: bool = True) -> Decimal | None:
        if market_order and self.market_amount_max is not None:
            return self.market_amount_max
        return self.amount_max

    def get_min_notional(self) -> Decimal | None:
        return self.cost_min

    def normalize_quantity(self, qty: Decimal | float | str, *, market_order: bool = True) -> Decimal:
        q = qty if isinstance(qty, Decimal) else Decimal(str(qty))
        if q <= 0:
            return Decimal(0)
        step = self.get_step_size(market_order=market_order)
        if step is None or step <= 0:
            prec = self.amount_precision
            if prec is not None and prec >= 0:
                quant = Decimal(1).scaleb(-prec)
                return q.quantize(quant, rounding=ROUND_DOWN)
            return q
        n = (q / step).to_integral_value(rounding=ROUND_DOWN)
        return n * step

    def validate_quantity(self, qty: Decimal | float | str, *, market_order: bool = True) -> tuple[bool, str]:
        q = qty if isinstance(qty, Decimal) else Decimal(str(qty))
        if q <= 0:
            return False, "quantity must be > 0"
        mn = self.get_min_qty(market_order=market_order)
        mx = self.get_max_qty(market_order=market_order)
        if mn is not None and q < mn:
            return False, f"quantity {q} < min {mn}"
        if mx is not None and q > mx:
            return False, f"quantity {q} > max {mx}"
        step = self.get_step_size(market_order=market_order)
        if step is not None and step > 0:
            rem = (q / step) % 1
            if rem != 0:
                return False, f"quantity {q} not aligned to step {step}"
        return True, "ok"


def extract_market_constraints(market: dict[str, Any]) -> MarketConstraints:
    symbol = str(market.get("symbol") or "")
    base = str(market.get("base") or "").upper()
    quote = str(market.get("quote") or "").upper()
    active = bool(market.get("active", True))
    limits = market.get("limits") or {}
    amount_lim = limits.get("amount") or {}
    cost_lim = limits.get("cost") or {}
    price_lim = limits.get("price") or {}
    precision = market.get("precision") or {}
    amount_prec = precision.get("amount")
    price_prec = precision.get("price")
    amount_prec_int, amount_step_from_prec = _precision_fields(amount_prec)
    price_prec_int, price_tick_from_prec = _precision_fields(price_prec)
    amount_min = _to_decimal(amount_lim.get("min"))
    amount_max = _to_decimal(amount_lim.get("max"))
    amount_step = amount_step_from_prec
    cost_min = _to_decimal(cost_lim.get("min"))
    cost_max = _to_decimal(cost_lim.get("max"))
    price_tick = price_tick_from_prec or _to_decimal(price_lim.get("min"))
    market_amount_min = amount_min
    market_amount_max = amount_max
    market_amount_step = amount_step
    info = market.get("info") or {}
    filters = info.get("filters") if isinstance(info, dict) else None
    if isinstance(filters, list):
        for f in filters:
            if not isinstance(f, dict):
                continue
            ft = str(f.get("filterType") or f.get("filter_type") or "").upper()
            if ft == "LOT_SIZE":
                amount_min = _to_decimal(f.get("minQty")) or amount_min
                amount_max = _to_decimal(f.get("maxQty")) or amount_max
                amount_step = _to_decimal(f.get("stepSize")) or amount_step
            elif ft == "MARKET_LOT_SIZE":
                market_amount_min = _to_decimal(f.get("minQty")) or market_amount_min
                market_amount_max = _to_decimal(f.get("maxQty")) or market_amount_max
                market_amount_step = _to_decimal(f.get("stepSize")) or market_amount_step
            elif ft in ("NOTIONAL", "MIN_NOTIONAL"):
                cost_min = _to_decimal(f.get("minNotional")) or _to_decimal(f.get("notional")) or cost_min
                cost_max = _to_decimal(f.get("maxNotional")) or cost_max
            elif ft == "PRICE_FILTER":
                price_tick = _to_decimal(f.get("tickSize")) or price_tick
    return MarketConstraints(
        symbol=symbol,
        base=base,
        quote=quote,
        active=active,
        amount_min=amount_min,
        amount_max=amount_max,
        amount_step=amount_step,
        cost_min=cost_min,
        cost_max=cost_max,
        price_tick=price_tick,
        amount_precision=amount_prec_int,
        price_precision=price_prec_int,
        market_amount_min=market_amount_min,
        market_amount_max=market_amount_max,
        market_amount_step=market_amount_step,
        raw_info=info if isinstance(info, dict) else None,
    )
