"""Candidate ranking across tradeable universe — TOP-1 is not auto-trade."""

from __future__ import annotations

from dataclasses import dataclass

from tko.core.types import Signal
from tko.strategy.btc import TradeDecision
from tko.strategy.edge import EdgeEstimate, estimate_edge


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    symbol: str
    base: str
    quote: str
    decision: TradeDecision
    edge: EdgeEstimate
    score: float


def rank_candidates(
    items: list[tuple[str, str, str, TradeDecision, float]],
) -> list[RankedCandidate]:
    ranked: list[RankedCandidate] = []
    for symbol, base, quote, decision, spread_pct in items:
        edge = estimate_edge(
            signal=decision.signal,
            strength=decision.strength,
            spread_pct=spread_pct,
        )
        score = edge.net_edge_pct if edge.pass_gate else -1e9
        if decision.signal not in (Signal.BUY, Signal.SELL):
            score = -1e9
        ranked.append(
            RankedCandidate(
                symbol=symbol,
                base=base,
                quote=quote,
                decision=decision,
                edge=edge,
                score=score,
            )
        )
    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked


def best_tradeable(ranked: list[RankedCandidate]) -> RankedCandidate | None:
    for c in ranked:
        if c.edge.pass_gate and c.decision.signal in (Signal.BUY, Signal.SELL) and c.score > 0:
            return c
    return None
