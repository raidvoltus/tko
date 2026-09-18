"""Lightweight ensemble — evidence fusion only, no order authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EnsembleOutput:
    confluence_score: float  # [-1, 1]
    strategy_weight: float
    model_weight: float
    disagreement: float
    version: str = "v1-weighted"


class Ensemble:
    VERSION = "v1-weighted"

    def __init__(self, strategy_weight: float = 0.7, model_weight: float = 0.3):
        w = strategy_weight + model_weight
        self.sw = strategy_weight / w if w else 0.7
        self.mw = model_weight / w if w else 0.3

    def fuse(
        self,
        strategy_composite: float,
        model_score: Optional[float] = None,
    ) -> EnsembleOutput:
        if model_score is None:
            return EnsembleOutput(
                confluence_score=float(strategy_composite),
                strategy_weight=1.0,
                model_weight=0.0,
                disagreement=0.0,
            )
        ms = float(model_score)
        conf = self.sw * float(strategy_composite) + self.mw * ms
        disagree = abs(float(strategy_composite) - ms)
        return EnsembleOutput(
            confluence_score=max(-1.0, min(1.0, conf)),
            strategy_weight=self.sw,
            model_weight=self.mw,
            disagreement=disagree,
        )
