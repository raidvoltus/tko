from decimal import Decimal

from tko.exchange.constraints import extract_market_constraints


def _sample_market(**overrides):
    m = {
        "symbol": "BTC/IDR", "base": "BTC", "quote": "IDR", "active": True,
        "limits": {"amount": {"min": 0.0001, "max": 100}, "cost": {"min": 10000, "max": None}, "price": {"min": 1}},
        "precision": {"amount": 0.0001, "price": 1},
        "info": {"filters": [
            {"filterType": "LOT_SIZE", "minQty": "0.00010000", "maxQty": "100.00000000", "stepSize": "0.00010000"},
            {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00020000", "maxQty": "50.00000000", "stepSize": "0.00010000"},
            {"filterType": "NOTIONAL", "minNotional": "15000.00"},
        ]},
    }
    m.update(overrides)
    return m

def test_extract_prefers_market_lot_size():
    c = extract_market_constraints(_sample_market())
    assert c.get_min_qty(market_order=True) == Decimal("0.0002")
    assert c.get_step_size(market_order=True) == Decimal("0.0001")
    assert c.get_min_notional() == Decimal("15000.00")

def test_normalize_floors_never_rounds_up():
    c = extract_market_constraints(_sample_market())
    assert c.normalize_quantity(Decimal("0.00025"), market_order=True) == Decimal("0.0002")
    assert c.normalize_quantity("0.0010") == Decimal("0.0010")

def test_zero_after_normalization_rejected():
    c = extract_market_constraints(_sample_market())
    n = c.normalize_quantity(Decimal("0.00005"), market_order=True)
    assert n == Decimal(0)
    ok, _ = c.validate_quantity(n)
    assert not ok

def test_min_notional_violation():
    c = extract_market_constraints(_sample_market())
    assert not c.validate_notional(Decimal(1000))[0]
    assert c.validate_notional(Decimal(20000))[0]

def test_missing_metadata_active_false():
    c = extract_market_constraints(_sample_market(active=False))
    assert c.active is False
