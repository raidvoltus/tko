"""Research-paper evaluation: friction, DSR, WFE, Sortino/Calmar, strategy harness."""
import numpy as np

from src.evaluation.friction import IDR_SPOT_FRICTION, USDT_SPOT_FRICTION
from src.evaluation.metrics_advanced import (
    calmar_ratio,
    deflated_sharpe_ratio,
    research_scorecard,
    sortino_ratio,
    walk_forward_efficiency,
)
from src.evaluation.strategies_harness import (
    apply_friction_pnl,
    grid_trend_trials,
    strategy_dca,
    strategy_hybrid,
    strategy_mean_reversion,
    strategy_trend_ema,
)


def test_idr_round_trip_near_paper():
    # paper ≈ 0.6544% without slip; with slip 0.1% each side higher
    rt = IDR_SPOT_FRICTION.round_trip_pct()
    assert 0.65 < rt < 1.2  # includes default slip


def test_usdt_friction_positive():
    assert USDT_SPOT_FRICTION.round_trip_pct() > 0.3


def test_sortino_calmar_finite():
    rng = np.random.default_rng(0)
    pnls = rng.normal(0.05, 1.0, 200)
    assert np.isfinite(sortino_ratio(pnls))
    assert np.isfinite(calmar_ratio(pnls))


def test_dsr_bounds():
    dsr = deflated_sharpe_ratio(2.0, n_trials=100, n_observations=250)
    assert 0.0 <= dsr <= 1.0
    dsr_noise = deflated_sharpe_ratio(0.1, n_trials=500, n_observations=50)
    assert dsr_noise < dsr


def test_wfe():
    assert walk_forward_efficiency(70, 100) == 0.7
    assert walk_forward_efficiency(40, 100) == 0.4


def test_strategies_and_friction_pnl():
    rng = np.random.default_rng(1)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, 300)))
    for fn in (strategy_dca, strategy_trend_ema, strategy_mean_reversion, strategy_hybrid):
        sig = fn(closes) if fn is not strategy_dca else strategy_dca(len(closes))
        pnl = apply_friction_pnl(closes, sig.positions, IDR_SPOT_FRICTION.round_trip_pct())
        assert len(pnl) == len(closes)


def test_grid_and_dsr_multiple_testing():
    rng = np.random.default_rng(2)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, 400)))
    trials = grid_trend_trials(closes, IDR_SPOT_FRICTION.round_trip_pct())
    assert len(trials) >= 3
    # pick best by sum pnl — then deflate
    best = max(trials, key=lambda t: float(np.nansum(t["pnls"])))
    sc = research_scorecard(best["pnls"], n_trials=len(trials), is_pnl=100.0, oos_pnl=60.0)
    assert "dsr" in sc and "wfe" in sc and sc["wfe"] == 0.6


def test_no_execution_in_research_modules():
    from pathlib import Path

    for p in (
        "src/evaluation/friction.py",
        "src/evaluation/metrics_advanced.py",
        "src/evaluation/strategies_harness.py",
    ):
        t = Path(p).read_text()
        assert "new_order" not in t
        assert "RestClient" not in t
