"""Adversarial + boundary RiskEngine tests — fail-closed, authoritative reduce-only."""
import threading

from src.risk.engine import RiskEngine, RiskLimits, RiskMode, RiskStateHealth


def _reconciled(**kw):
    lim = RiskLimits(max_stale_market_sec=9999, **kw)
    r = RiskEngine(lim)
    r.ws_connected = True
    r.orderbook_synced = True
    r.update_market_ts("BTC_USDT")
    assert r.apply_authoritative_snapshot(equity=10_000.0, positions={}, daily_pnl=0.0)
    return r


def test_unreconciled_denies():
    r = RiskEngine(RiskLimits(max_stale_market_sec=9999))
    r.ws_connected = True
    r.orderbook_synced = True
    r.update_market_ts("BTC_USDT")
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert any("STATE_" in b for b in st.blocked_by)


def test_kill_switch_blocks():
    r = _reconciled()
    r.activate_kill_switch("test")
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "KILL_SWITCH" in st.blocked_by


def test_max_order_value():
    r = _reconciled(max_order_value_usdt=50)
    st = r.check("BTC_USDT", 0, 100, 1000)
    assert not st.allowed
    assert "MAX_ORDER_VALUE" in st.blocked_by


def test_ok_when_within_limits():
    r = _reconciled(max_order_value_usdt=200)
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert st.allowed, st.reason


def test_nan_notional_blocked():
    r = _reconciled()
    st = r.check("BTC_USDT", 0, float("nan"), 1000)
    assert not st.allowed
    assert "INVALID_NOTIONAL" in st.blocked_by


def test_zero_notional_blocked():
    r = _reconciled()
    st = r.check("BTC_USDT", 0, 0, 1000)
    assert not st.allowed


def test_invalid_equity_marks_invalid():
    r = _reconciled()
    r.update_equity(float("nan"))
    assert r.state_health == RiskStateHealth.INVALID
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed


def test_drawdown_halt():
    r = _reconciled(max_drawdown_pct=0.10, warn_drawdown_pct=0.05, reduce_drawdown_pct=0.08)
    r.update_equity(850)  # peak 10000 → huge dd
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed


def test_reduce_only_authoritative_blocks_buy():
    r = _reconciled(reduce_drawdown_pct=0.05, warn_drawdown_pct=0.02, max_drawdown_pct=0.50)
    r.update_equity(9400)  # 6% DD from 10000
    assert r.risk_mode == RiskMode.REDUCE_ONLY
    # spoof reduce_only_intent must not help BUY
    st = r.check("BTC_USDT", 0, 10, 1000, reduce_only_intent=True)
    assert not st.allowed
    assert "REDUCE_ONLY_MODE" in st.blocked_by or "BUY_INCREASES_RISK" in st.reason


def test_reduce_only_sell_reduces_long():
    r = _reconciled(reduce_drawdown_pct=0.05, warn_drawdown_pct=0.02, max_drawdown_pct=0.50)
    r.update_equity(9400)
    r.update_position("BTC_USDT", 50.0)
    st = r.check("BTC_USDT", 1, 20, 1000)
    assert st.allowed, st.reason
    assert st.is_risk_reducing


def test_reduce_only_sell_exceeds_long_blocked():
    r = _reconciled(reduce_drawdown_pct=0.05, warn_drawdown_pct=0.02, max_drawdown_pct=0.50)
    r.update_equity(9400)
    r.update_position("BTC_USDT", 10.0)
    st = r.check("BTC_USDT", 1, 50, 1000)
    assert not st.allowed


def test_spot_short_flat_sell_blocked():
    r = _reconciled()
    st = r.check("BTC_USDT", 1, 10, 1000)
    assert not st.allowed
    assert "SPOT_SHORT_NOT_ALLOWED" in st.blocked_by


def test_reduce_only_intent_spoof_cannot_open():
    r = _reconciled(reduce_drawdown_pct=0.05, warn_drawdown_pct=0.02, max_drawdown_pct=0.50)
    r.update_equity(9400)
    st = r.check("BTC_USDT", 0, 10, 1000, reduce_only_intent=True)
    assert not st.allowed


def test_admit_reservation_and_release():
    r = _reconciled(max_orders_per_minute=5, max_orders_per_day=100)
    st = r.admit("BTC_USDT", 0, 10, 1000)
    assert st.allowed and st.reservation_id
    before = r.orders_today
    r.release_reservation(st.reservation_id, safe=True)
    assert r.orders_today == before - 1


def test_concurrent_admit_rate_limit():
    r = _reconciled(max_orders_per_minute=3, max_orders_per_day=100)
    results = []

    def worker():
        results.append(r.admit("BTC_USDT", 0, 5, 1000).allowed)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for x in results if x) <= 3


def test_max_orders_per_day():
    r = _reconciled(max_orders_per_day=2, max_orders_per_minute=100)
    assert r.admit("BTC_USDT", 0, 10, 1000).allowed
    assert r.admit("BTC_USDT", 0, 10, 1000).allowed
    st = r.admit("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert "MAX_ORDERS_PER_DAY" in st.blocked_by


def test_concentration_multi_symbol():
    r = _reconciled(max_symbol_concentration=0.4, max_exposure_usdt=1000)
    r.update_position("ETH_USDT", 400)
    st = r.check("BTC_USDT", 0, 400, 1000)
    assert not st.allowed
    assert "MAX_SYMBOL_CONCENTRATION" in st.blocked_by


def test_kill_reset_requires_operator_and_reconcile():
    r = _reconciled()
    r.activate_kill_switch("x")
    assert not r.reset_kill_switch(operator_approved=False)
    assert r.kill_switch
    assert r.reset_kill_switch(operator_approved=True, approval_ref="ops")
    assert not r.kill_switch


def test_kill_reset_denied_if_invalid_state():
    r = _reconciled()
    r.activate_kill_switch("x")
    r.state_health = RiskStateHealth.INVALID
    assert not r.reset_kill_switch(operator_approved=True, approval_ref="ops")


def test_hwm_never_decreases_on_equity_drop():
    r = _reconciled()
    r.update_equity(9000)
    peak = r.equity_peak
    assert peak >= 10000  # snapshot set peak
    r.update_equity(8000)
    assert r.equity_peak == peak


def test_reconciliation_failure_blocks():
    r = _reconciled()
    r.mark_reconciliation_failed("rest_error")
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed


def test_unknown_order_blocks():
    r = _reconciled()
    r.mark_unknown_order("c1")
    st = r.check("BTC_USDT", 0, 10, 1000)
    assert not st.allowed
    assert r.state_health == RiskStateHealth.UNKNOWN


def test_ip_ban_418_kill():
    r = _reconciled()
    r.record_api_error(418)
    assert r.kill_switch


def test_boundary_warn_reduce_halt():
    r = _reconciled(warn_drawdown_pct=0.05, reduce_drawdown_pct=0.10, max_drawdown_pct=0.15)
    r.equity_peak = 1000
    r.equity = 960  # 4%
    r._refresh_mode_from_drawdown()
    assert r.risk_mode == RiskMode.NORMAL
    r.equity = 940  # 6%
    r._refresh_mode_from_drawdown()
    assert r.risk_mode == RiskMode.WARNING
    r.equity = 890  # 11%
    r._refresh_mode_from_drawdown()
    assert r.risk_mode == RiskMode.REDUCE_ONLY
    r.equity = 840  # 16%
    r._refresh_mode_from_drawdown()
    assert r.risk_mode == RiskMode.HALT


def test_session_day_wib_offset():
    r = RiskEngine(RiskLimits(session_tz_offset_hours=7))
    key = r._session_day_key()
    assert len(key) == 10
