"""Production trading policy — LIVE only.

No PAPER/SHADOW/SIMULATION/DRY_RUN/DEMO as production execution modes.
"""
from __future__ import annotations

ALLOWED_MODES = frozenset({"LIVE"})
REJECTED_MODES = frozenset({
    "PAPER", "SHADOW", "SIMULATION", "DRY_RUN", "DEMO", "MOCK", "VIRTUAL", "TEST_EXECUTION",
})


def is_production_trading_mode(mode: str) -> bool:
    return str(mode).upper() == "LIVE"


def require_live(mode: str) -> str:
    m = str(mode).upper()
    if m in REJECTED_MODES or m != "LIVE":
        raise ValueError(
            f"INVALID_CONFIGURATION: mode={m} rejected; only LIVE is allowed"
        )
    return "LIVE"
