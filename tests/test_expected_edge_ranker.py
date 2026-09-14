"""Expected edge gate and candidate ranking — fail-closed."""

from tko.core.types import Signal
from tko.strategy.btc import TradeDecision
from tko.strategy.edge import estimate_edge
from tko.strategy.ranker import best_tradeable, rank_candidates


def test_hold_fails_edge_gate():
    e = estimate_edge(signal=Signal.HOLD, strength=1.0)
    assert e.pass_gate is False


def test_weak_strength_fails():
    e = estimate_edge(signal=Signal.BUY, strength=0.0)
    assert e.pass_gate is False


def test_strong_buy_can_pass():
    e = estimate_edge(signal=Signal.BUY, strength=0.9, spread_pct=0.01)
    assert e.pass_gate is True
    assert e.net_edge_pct > 0


def test_wide_spread_fails():
    e = estimate_edge(signal=Signal.BUY, strength=0.5, spread_pct=5.0)
    assert e.pass_gate is False


def test_ranker_prefers_higher_net_edge():
    d1 = TradeDecision(Signal.BUY, "a", 0.4, 100.0)
    d2 = TradeDecision(Signal.BUY, "b", 0.95, 100.0)
    ranked = rank_candidates(
        [
            ("AAA/USDT", "AAA", "USDT", d1, 0.05),
            ("BBB/USDT", "BBB", "USDT", d2, 0.05),
        ]
    )
    best = best_tradeable(ranked)
    assert best is not None
    assert best.symbol == "BBB/USDT"


def test_ranker_no_trade_when_all_fail():
    d = TradeDecision(Signal.HOLD, "none", 0.0, 1.0)
    ranked = rank_candidates([("X/USDT", "X", "USDT", d, 0.0)])
    assert best_tradeable(ranked) is None
