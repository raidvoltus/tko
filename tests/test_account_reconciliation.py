"""Authoritative account reconciliation + LIVE-only policy tests."""
from decimal import Decimal

import pytest

from src.execution.production_policy import REJECTED_MODES, require_live
from src.portfolio.account_state import (
    AccountReconciler,
    AccountStatus,
    parse_account_assets,
)


def test_parse_account_assets_usdt_tko():
    body = {
        "code": 0,
        "data": {
            "accountAssets": [
                {"asset": "USDT", "free": "1250.5", "locked": "200"},
                {"asset": "TKO", "free": "850", "locked": "100"},
            ]
        },
    }
    assets, err, n = parse_account_assets(body)
    assert n == 2 and not err
    assert assets["USDT"].total == Decimal("1450.5")
    assert assets["TKO"].free == Decimal("850")
    assert assets["TKO"].total == Decimal("950")


def test_parse_10_plus_assets():
    rows = [{"asset": f"A{i}", "free": str(i + 1), "locked": "0"} for i in range(12)]
    assets, err, n = parse_account_assets({"data": {"accountAssets": rows}})
    assert n == 12 and len(assets) == 12


def test_zero_assets_valid():
    assets, err, n = parse_account_assets({"data": {"accountAssets": []}})
    assert n == 0 and assets == {}


def test_malformed_and_negative():
    body = {
        "data": {
            "accountAssets": [
                {"asset": "USDT", "free": "10", "locked": "0"},
                {"asset": "BAD", "free": "NaN", "locked": "0"},
                {"asset": "NEG", "free": "-1", "locked": "0"},
                "notadict",
            ]
        }
    }
    assets, err, n = parse_account_assets(body)
    assert "USDT" in assets
    assert any("NaN" in e or "non_finite" in e or "non_decimal" in e for e in err)
    assert any("negative" in e for e in err)


def test_missing_account_assets():
    assets, err, n = parse_account_assets({"data": {}})
    assert "missing_accountAssets" in err


def test_reconciler_valid():
    r = AccountReconciler()
    st = r.apply_rest_snapshot(
        {"data": {"accountAssets": [{"asset": "USDT", "free": "100", "locked": "0"}]}}
    )
    assert st.status == AccountStatus.VALID
    assert st.is_tradeable()
    assert st.available_quote("USDT") == Decimal("100")


def test_reconciler_failed_unknown():
    r = AccountReconciler()
    st = r.mark_failed("network")
    assert st.status == AccountStatus.UNKNOWN
    assert not st.is_tradeable()
    assert st.assets == {}


def test_out_of_order_stream():
    r = AccountReconciler()
    r.apply_rest_snapshot(
        {"data": {"accountAssets": [{"asset": "USDT", "free": "1", "locked": "0"}]}}
    )
    r.state.last_exchange_event_at = 1000.0
    r.apply_user_stream_balance("USDT", "2", "0", event_ts=10.0)
    assert any("out_of_order" in e for e in r.state.errors)


def test_valuation_unknown():
    r = AccountReconciler()
    r.apply_rest_snapshot(
        {"data": {"accountAssets": [{"asset": "BTC", "free": "1", "locked": "0"}]}}
    )
    r.apply_valuations({})  # no price
    assert r.state.assets["BTC"].valuation_usdt is None
    eq, unpriced = r.state.equity_usdt()
    assert "BTC" in unpriced


def test_require_live_rejects_all_non_live():
    for m in REJECTED_MODES:
        with pytest.raises(ValueError):
            require_live(m)
    assert require_live("LIVE") == "LIVE"
    assert require_live("live") == "LIVE"


def test_execution_manager_rejects_paper():
    from src.execution.manager import ExecutionManager
    from src.risk.engine import RiskEngine, RiskLimits
    from src.tokocrypto.rest import RestClient

    em = ExecutionManager(RestClient(), RiskEngine(RiskLimits()), mode="LIVE")
    with pytest.raises(ValueError):
        em.set_mode("PAPER")
    with pytest.raises(ValueError):
        em.set_mode("SHADOW")
    with pytest.raises(ValueError):
        em.set_mode("SIMULATION")


def test_no_paper_in_production_policy_allowed():
    from src.execution.production_policy import ALLOWED_MODES
    assert ALLOWED_MODES == frozenset({"LIVE"})
