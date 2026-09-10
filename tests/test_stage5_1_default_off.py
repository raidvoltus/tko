"""Stage 5.1 default-OFF regression: ML governor/pool must not alter production path."""

from __future__ import annotations

from tko.core.config import Settings
from tko.core.types import Signal
from tko.ml.filter import MlSignalFilter
from tko.ml.governor import ComputationalGovernor, GovernorConfig, ResourceSnapshot
from tko.strategy.btc import TradeDecision


def test_settings_stage5_1_defaults_off() -> None:
    s = Settings(live_mode=True, min_quote_balance=1)
    assert s.ml_filter_enabled is False
    assert s.ml_governor_enabled is False
    assert s.ml_pool_max_models == 5
    assert s.ml_n_jobs == 1
    assert s.ml_max_depth == 4
    assert s.ml_max_trees == 40


def test_filter_without_governor_unchanged() -> None:
    f = MlSignalFilter(enabled=False)
    out = f.filter(TradeDecision(Signal.BUY, "x", 0.5, 100.0), [])
    assert out.allow is True
    assert out.reason == "ml_filter_disabled"


def test_filter_governor_safe_exit_blocks_buy_only() -> None:
    gov = ComputationalGovernor(
        GovernorConfig(enabled=True, safe_exit_on_pressure=True)
    )
    gov.observe(ResourceSnapshot(ram_used_pct=0.99, latency_ms=10.0))
    assert gov.should_hold_only()

    f = MlSignalFilter(enabled=True, model=None, governor=gov)
    buy = f.filter(TradeDecision(Signal.BUY, "x", 0.5, 100.0), [])
    assert buy.allow is False
    assert "governor_safe_exit" in buy.reason

    sell = f.filter(TradeDecision(Signal.SELL, "x", 0.5, 100.0), [])
    assert sell.allow is True


def test_filter_governor_none_does_not_require_ml_deps() -> None:
    """Import and construct with governor=None must not load backends."""
    f = MlSignalFilter(enabled=False, governor=None, pool=None, ensemble=None)
    assert f.governor is None
    assert f.pool is None
    assert f.ensemble is None
