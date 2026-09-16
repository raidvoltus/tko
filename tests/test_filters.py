"""Exchange filter / rounding tests."""
from decimal import Decimal

from src.execution.filters import SymbolFilters, normalize_order, round_step


def test_round_step_down():
    assert round_step(Decimal("1.23456"), Decimal("0.001")) == Decimal("1.234")


def test_normalize_lot_size():
    sf = SymbolFilters(
        symbol="BTC_USDT",
        qty_step=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        max_qty=Decimal("100"),
        price_tick=Decimal("0.01"),
        min_price=Decimal("1"),
        max_price=Decimal("1000000"),
        min_notional=Decimal("10"),
    )
    ok, norm, err = normalize_order(sf, 0, 1, quantity="0.0015", price="50000")
    assert ok
    assert Decimal(norm["quantity"]) == Decimal("0.001")
    assert "price" in norm


def test_reject_below_min_notional():
    sf = SymbolFilters(
        symbol="BTC_USDT",
        qty_step=Decimal("0.0001"),
        min_qty=Decimal("0.0001"),
        max_qty=Decimal("100"),
        price_tick=Decimal("0.01"),
        min_notional=Decimal("10"),
    )
    ok, norm, err = normalize_order(sf, 0, 1, quantity="0.0001", price="10")
    assert not ok
    assert "minNotional" in err or "notional" in err.lower()
