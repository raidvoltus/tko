"""Portfolio rotation / net opportunity / route tests."""
from src.portfolio.rotation import (
    AssetScanner,
    OpportunityScorer,
    PortfolioRotationEngine,
    RouteOptimizer,
)


def test_net_opportunity_formula():
    s = OpportunityScorer()
    opp = s.score("BTC", "ETH", expected_return_pct=3.0, spread_pct=0.1)
    # 3 - fee0.2 - spread0.1 - slip - impact - exec - vol < 3
    assert opp.net_opportunity_pct < opp.expected_return_pct
    assert opp.fee_pct > 0


def test_route_direct_and_via():
    r = RouteOptimizer()
    r.set_pairs([
        {"symbol": "BTC_USDT", "base": "BTC", "quote": "USDT"},
        {"symbol": "ETH_USDT", "base": "ETH", "quote": "USDT"},
        {"symbol": "BTC_ETH", "base": "BTC", "quote": "ETH"},
    ])
    assert r.find_route("BTC", "ETH") == ["BTC", "ETH"]
    assert r.find_route("BTC", "SOL")  # via intermediate may be length 3 or 2


def test_scanner_bounds():
    sc = AssetScanner(max_symbols=5)
    info = {
        "symbols": [
            {"symbol": f"A{i}_USDT", "baseAsset": f"A{i}", "quoteAsset": "USDT", "status": "TRADING"}
            for i in range(20)
        ]
    }
    out = sc.scan(info)
    assert len(out) <= 5


def test_plan_hold_without_edge():
    eng = PortfolioRotationEngine(min_edge_pct=5.0)
    snap = eng.build_snapshot(
        {"USDT": {"free": 100, "locked": 0}, "BTC": {"free": 0.01, "locked": 0}},
        {"USDT": 1.0, "BTC": 50000},
    )
    plan = eng.plan_cycle(snap, {"USDT": 0.0, "BTC": 0.1, "ETH": 0.2})
    assert plan.hold is True or plan.selected is None or plan.selected.net_opportunity_pct < 5
