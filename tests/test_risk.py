"""Risk engine tests."""
from src.risk.engine import RiskEngine, RiskLimits


def test_kill_switch_blocks():
    r = RiskEngine()
    r.activate_kill_switch("test")
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "KILL_SWITCH" in st.blocked_by


def test_max_order_value():
    r = RiskEngine(RiskLimits(max_order_value_usdt=50))
    r.ws_connected = True
    r.orderbook_synced = True
    r.update_market_ts("BTC_USDT")
    st = r.check("BTC_USDT", 0, 100, 1000)
    assert not st.allowed
    assert "MAX_ORDER_VALUE" in st.blocked_by


def test_ok_when_within_limits():
    r = RiskEngine(RiskLimits(max_order_value_usdt=200, max_stale_market_sec=9999))
    r.ws_connected = True
    r.orderbook_synced = True
    r.update_market_ts("BTC_USDT")
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert st.allowed
