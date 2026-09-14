"""Expected-edge gate: trade only if estimated edge > cost + safety margin.

Strategy/ML produce signal strength; this module converts to a conservative
edge estimate and compares against fees/spread/slippage. Fail-closed: if
inputs invalid → edge=0 → NO TRADE.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from tko.core.types import Signal

DEFAULT_FEE_PCT = 0.10
DEFAULT_SLIPPAGE_PCT = 0.05
DEFAULT_SAFETY_MARGIN_PCT = 0.10


@dataclass(frozen=True, slots=True)
class EdgeEstimate:
    signal: Signal
    strength: float
    estimated_edge_pct: float
    estimated_cost_pct: float
    net_edge_pct: float
    pass_gate: bool
    reason: str


def estimate_edge(
    *,
    signal: Signal,
    strength: float,
    spread_pct: float = 0.0,
    fee_pct_per_side: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    safety_margin_pct: float = DEFAULT_SAFETY_MARGIN_PCT,
    expected_move_pct: float | None = None,
) -> EdgeEstimate:
    if signal not in (Signal.BUY, Signal.SELL):
        return EdgeEstimate(
            signal=signal,
            strength=float(strength or 0.0),
            estimated_edge_pct=0.0,
            estimated_cost_pct=0.0,
            net_edge_pct=0.0,
            pass_gate=False,
            reason="signal_not_actionable",
        )
    try:
        s = float(strength)
    except (TypeError, ValueError):
        s = 0.0
    if s <= 0 or s > 1.0 or math.isnan(s):
        return EdgeEstimate(
            signal=signal,
            strength=s,
            estimated_edge_pct=0.0,
            estimated_cost_pct=0.0,
            net_edge_pct=0.0,
            pass_gate=False,
            reason="invalid_strength",
        )
    try:
        sp = max(0.0, float(spread_pct))
    except (TypeError, ValueError):
        sp = 0.0

    if expected_move_pct is None:
        gross = 0.3 + 1.2 * s
    else:
        try:
            gross = abs(float(expected_move_pct))
        except (TypeError, ValueError):
            return EdgeEstimate(signal, s, 0.0, 0.0, 0.0, False, "invalid_expected_move")

    cost = 2.0 * float(fee_pct_per_side) + sp + float(slippage_pct)
    net = gross - cost - float(safety_margin_pct)
    ok = net > 0
    return EdgeEstimate(
        signal=signal,
        strength=s,
        estimated_edge_pct=gross,
        estimated_cost_pct=cost,
        net_edge_pct=net,
        pass_gate=ok,
        reason="ok" if ok else f"net_edge={net:.4f}%<=0 (gross={gross:.4f} cost={cost:.4f})",
    )
