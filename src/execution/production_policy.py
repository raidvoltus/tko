"""
Production execution policy.

Intelligence shadow (champion/challenger evaluation) is allowed.
Fake fill / PAPER execution is NOT production trading authority.

LIVE orders: RiskEngine → ExecutionManager → RestClient order API only.
"""
from __future__ import annotations

PRODUCTION_MODES = frozenset({"LIVE"})
NON_PRODUCTION_MODES = frozenset({"PAPER", "SHADOW"})


def is_production_trading_mode(mode: str) -> bool:
    return str(mode).upper() == "LIVE"


def assert_production_mode_or_raise(mode: str, allow_non_production: bool = False) -> None:
    m = str(mode).upper()
    if m in PRODUCTION_MODES:
        return
    if allow_non_production and m in NON_PRODUCTION_MODES:
        return
    raise RuntimeError(
        f"PRODUCTION_POLICY: mode={m} is not LIVE; "
        "PAPER/SHADOW are non-production (tests/offline only)"
    )
