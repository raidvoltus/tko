"""Cost-adjusted expected edge. Strategy score ≠ calibrated expected return."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpectedEdge:
    gross_edge_pct: float
    fee_pct: float
    spread_pct: float
    slippage_pct: float
    impact_pct: float
    exec_risk_pct: float
    vol_risk_pct: float
    net_edge_pct: float
    calibrated: bool = False
    source: str = "heuristic"

    def tradeable(self, min_net: float = 0.0) -> bool:
        return self.net_edge_pct > min_net


def cost_adjusted_edge(
    gross_edge_pct: float,
    fee_pct: float = 0.1,
    spread_pct: float = 0.05,
    slippage_pct: float = 0.05,
    impact_pct: float = 0.02,
    exec_risk_pct: float = 0.03,
    vol_risk_pct: float = 0.05,
    source: str = "heuristic",
    calibrated: bool = False,
) -> ExpectedEdge:
    """
    net = gross − fee − spread − slippage − impact − exec_risk − vol_risk
    Never treat raw strategy composite as calibrated probability/return.
    """
    costs = fee_pct + spread_pct + slippage_pct + impact_pct + exec_risk_pct + vol_risk_pct
    net = float(gross_edge_pct) - costs
    return ExpectedEdge(
        gross_edge_pct=float(gross_edge_pct),
        fee_pct=fee_pct,
        spread_pct=spread_pct,
        slippage_pct=slippage_pct,
        impact_pct=impact_pct,
        exec_risk_pct=exec_risk_pct,
        vol_risk_pct=vol_risk_pct,
        net_edge_pct=net,
        calibrated=calibrated,
        source=source,
    )


def strategy_score_to_gross_edge(composite: float, scale_pct: float = 2.5) -> float:
    """Map bounded composite [-1,1] to gross edge percent heuristic (uncalibrated)."""
    return float(composite) * scale_pct
