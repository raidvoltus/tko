"""
Governor — decision policy only.
BUY/SELL/HOLD/WAIT/NO_TRADE — NEVER submits orders.
Risk Engine remains absolute authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.decision.edge import ExpectedEdge
from src.decision.strategies import StrategyScores


@dataclass(frozen=True)
class GovernorDecision:
    action: str  # BUY SELL HOLD WAIT NO_TRADE
    reason: str
    strategy_score: float
    model_score: float
    confluence_score: float
    expected_net_edge_pct: float
    regime: str
    confidence: float


class Governor:
    VERSION = "v1-policy"

    def decide(
        self,
        scores: StrategyScores,
        model_score: Optional[float] = None,  # not calibrated probability
        edge: Optional[ExpectedEdge] = None,
        data_valid: bool = True,
        stale: bool = False,
        risk_blocked: bool = False,
        min_net_edge_pct: float = 0.0,
        min_confidence: float = 0.35,
    ) -> GovernorDecision:
        if not data_valid or scores.regime == "UNKNOWN":
            return GovernorDecision(
                "NO_TRADE", "UNKNOWN_OR_INVALID_DATA", scores.composite,
                float(model_score or 0.0), scores.composite, 0.0, scores.regime, scores.regime_confidence,
            )
        if stale:
            return GovernorDecision(
                "NO_TRADE", "STALE_DATA", scores.composite,
                float(model_score or 0.0), scores.composite, 0.0, scores.regime, scores.regime_confidence,
            )
        if risk_blocked:
            return GovernorDecision(
                "NO_TRADE", "RISK_BLOCKED", scores.composite,
                float(model_score or 0.0), scores.composite,
                edge.net_edge_pct if edge else 0.0, scores.regime, scores.regime_confidence,
            )
        if scores.regime_confidence < min_confidence:
            return GovernorDecision(
                "HOLD", "LOW_REGIME_CONFIDENCE", scores.composite,
                float(model_score or 0.0), scores.composite,
                edge.net_edge_pct if edge else 0.0, scores.regime, scores.regime_confidence,
            )

        net = edge.net_edge_pct if edge else 0.0
        if edge and not edge.tradeable(min_net_edge_pct):
            return GovernorDecision(
                "WAIT", "NO_NET_EDGE", scores.composite,
                float(model_score or 0.0), scores.composite, net, scores.regime, scores.regime_confidence,
            )

        # Confluence: strategy composite primary; model_score is evidence only
        conf = scores.composite
        if model_score is not None:
            conf = 0.7 * scores.composite + 0.3 * float(model_score)

        if scores.regime == "VOLATILE" and abs(conf) < 0.4:
            action, reason = "HOLD", "VOLATILE_LOW_CONFLUENCE"
        elif conf >= 0.25 and net > min_net_edge_pct:
            action, reason = "BUY", "CONFLUENCE_BULLISH"
        elif conf <= -0.25 and net > min_net_edge_pct:
            action, reason = "SELL", "CONFLUENCE_BEARISH"
        elif abs(conf) < 0.1:
            action, reason = "HOLD", "NEUTRAL"
        else:
            action, reason = "WAIT", "WEAK_CONFLUENCE"

        return GovernorDecision(
            action, reason, scores.composite, float(model_score or 0.0), conf, net,
            scores.regime, scores.regime_confidence,
        )
