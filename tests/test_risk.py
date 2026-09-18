"""Risk engine tests — absolute authority, fail-closed."""
import time

from src.risk.engine import RiskEngine, RiskLimits, RiskMode


def _fresh(**kw):
    lim = RiskLimits(max_stale_market_sec=9999, **kw)
    r = RiskEngine(lim)
    r.ws_connected = True
    r.orderbook_synced = True
    r.update_market_ts("BTC_USDT")
    return r


def test_kill_switch_blocks():
    r = _fresh()
    r.activate_kill_switch("test")
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "KILL_SWITCH" in st.blocked_by


def test_max_order_value():
    r = _fresh(max_order_value_usdt=50)
    st = r.check("BTC_USDT", 0, 100, 1000)
    assert not st.allowed
    assert "MAX_ORDER_VALUE" in st.blocked_by


def test_ok_when_within_limits():
    r = _fresh(max_order_value_usdt=200)
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert st.allowed


def test_nan_notional_blocked():
    r = _fresh()
    st = r.check("BTC_USDT", 0, float("nan"), 1000)
    assert not st.allowed
    assert "INVALID_NOTIONAL" in st.blocked_by


def test_zero_notional_blocked():
    r = _fresh()
    st = r.check("BTC_USDT", 0, 0, 1000)
    assert not st.allowed
    assert "ZERO_NOTIONAL" in st.blocked_by


def test_drawdown_halt():
    r = _fresh(max_drawdown_pct=0.10, warn_drawdown_pct=0.05, reduce_drawdown_pct=0.08)
    r.update_equity(1000)
    r.update_equity(850)  # 15% DD
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "MAX_DRAWDOWN" in st.blocked_by or "CIRCUIT_BREAKER" in st.blocked_by or "RISK_MODE_HALT" in st.blocked_by


def test_reduce_only_blocks_increase():
    r = _fresh(reduce_drawdown_pct=0.05, warn_drawdown_pct=0.02, max_drawdown_pct=0.50)
    r.update_equity(1000)
    r.update_equity(940)  # 6% DD → REDUCE_ONLY
    assert r.risk_mode == RiskMode.REDUCE_ONLY
    st = r.check("BTC_USDT", 0, 10, 1000, reduce_only_intent=False)
    assert not st.allowed
    assert "REDUCE_ONLY_MODE" in st.blocked_by
    st2 = r.check("BTC_USDT", 1, 10, 1000, reduce_only_intent=True)
    assert st2.allowed


def test_max_orders_per_day():
    r = _fresh(max_orders_per_day=2, max_orders_per_minute=100)
    r.record_order_attempt()
    r.record_order_attempt()
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "MAX_ORDERS_PER_DAY" in st.blocked_by


def test_max_orders_per_minute():
    r = _fresh(max_orders_per_minute=2, max_orders_per_day=100)
    r.record_order_attempt()
    r.record_order_attempt()
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "MAX_ORDERS_PER_MINUTE" in st.blocked_by


def test_concentration():
    r = _fresh(max_symbol_concentration=0.4, max_exposure_usdt=1000)
    r.update_position("ETH_USDT", 400)
    st = r.check("BTC_USDT", 0, 400, 1000, current_position_notional=0)
    # total after = 800, BTC share 400/800=0.5 > 0.4
    assert not st.allowed
    assert "MAX_SYMBOL_CONCENTRATION" in st.blocked_by


def test_consecutive_loss_cooldown():
    r = _fresh(max_consecutive_losses=2, consecutive_loss_cooldown_sec=60)
    r.update_pnl(-1)
    r.update_pnl(-1)
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "MAX_CONSECUTIVE_LOSSES" in st.blocked_by or "LOSS_COOLDOWN" in st.blocked_by


def test_daily_loss_trips_breaker():
    r = _fresh(max_daily_loss_usdt=10)
    r.update_pnl(-11)
    assert r.circuit_breaker
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed


def test_size_multiplier_advisory():
    r = _fresh(warn_drawdown_pct=0.05, reduce_drawdown_pct=0.20, max_drawdown_pct=0.50)
    r.update_equity(1000)
    r.update_equity(940)  # 6% → WARNING
    assert r.size_multiplier() == 0.5


def test_vol_halt():
    r = _fresh(max_realized_vol=0.05)
    r.set_realized_vol(0.09)
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "VOLATILITY_HALT" in st.blocked_by


def test_events_recorded():
    r = _fresh()
    r.activate_kill_switch("x")
    assert any(e.kind == "KILL_SWITCH" for e in r.events())
