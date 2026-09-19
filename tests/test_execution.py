"""Order lifecycle — LIVE only; no paper fill path in production manager."""
from decimal import Decimal

import pytest

from src.core.order_state import OrderState
from src.execution.filters import SymbolFilters
from src.execution.manager import ExecutionManager
from src.risk.engine import RiskEngine, RiskLimits
from src.tokocrypto.rest import RestClient


def _make_mgr():
    risk = RiskEngine(RiskLimits(max_stale_market_sec=99999, max_order_value_usdt=10000))
    risk.ws_connected = True
    risk.orderbook_synced = True
    risk.update_market_ts("BTC_USDT")
    risk.apply_authoritative_snapshot(equity=10_000.0, positions={}, daily_pnl=0.0)
    rest = RestClient()  # no real keys — LIVE submit will fail network, not paper-fill
    mgr = ExecutionManager(rest, risk, mode="LIVE")
    mgr.filters["BTC_USDT"] = SymbolFilters(
        symbol="BTC_USDT",
        qty_step=Decimal("0.001"),
        min_qty=Decimal("0.001"),
        max_qty=Decimal("10"),
        price_tick=Decimal("0.01"),
        min_notional=Decimal("5"),
    )
    return mgr


def test_rejects_non_live_mode():
    mgr = _make_mgr()
    with pytest.raises(ValueError):
        mgr.set_mode("PAPER")


def test_risk_blocks_order():
    mgr = _make_mgr()
    mgr.risk.activate_kill_switch("test")
    order = mgr.submit(
        symbol="BTC_USDT",
        side=0,
        order_type=1,
        quantity="0.01",
        price="50000",
        available_balance=1000,
    )
    assert order.state == OrderState.REJECTED
    assert any("KILL" in str(h) for h in order.history) or "KILL" in str(order.history)


def test_live_path_not_paper_fill():
    """Without exchange keys, LIVE does not pretend FILLED."""
    mgr = _make_mgr()
    order = mgr.submit(
        symbol="BTC_USDT",
        side=0,
        order_type=1,
        quantity="0.01",
        price="50000",
        available_balance=1000,
        reference_price=50000,
    )
    assert order.state != OrderState.FILLED
    assert order.mode == "LIVE"
