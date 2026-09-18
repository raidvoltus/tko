"""DECISION PLANE - regime, strategy confluence, signal. ML has no order authority."""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np

from src.decision.strategies import StrategyEngine, StrategyScores

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signal:
    signal_id: str
    cycle_id: str
    symbol: str
    action: str  # BUY / SELL / WAIT / HOLD
    probability: float
    expected_return_pct: float
    net_opportunity_pct: float
    regime: str
    feature_version: str
    model_hash: str
    timestamp: float
    ttl_sec: float
    signal_hash: str
    strategy_composite: float = 0.0
    regime_confidence: float = 0.0
    strategy_reason: str = ""

    def expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) > self.timestamp + self.ttl_sec


def _hash_signal_payload(payload: Dict[str, Any]) -> str:
    raw = str(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class RegimeClassifier:
    """Backward-compatible wrapper around StrategyEngine.regime."""

    def __init__(self):
        self._engine = StrategyEngine()

    def classify(self, closes: np.ndarray, vol_20: float = 0.0) -> str:
        regime, _, _ = self._engine.regime.classify(closes)
        return regime


class DecisionPlane:
    def __init__(self, feature_version: str = "v2"):
        self.feature_version = feature_version
        self.regime_clf = RegimeClassifier()
        self.strategies = StrategyEngine()
        self.last_signal: Optional[Signal] = None
        self.last_scores: Optional[StrategyScores] = None

    def evaluate_strategies(
        self,
        closes: np.ndarray,
        volumes: Optional[np.ndarray] = None,
        rsi: float = 50.0,
    ) -> StrategyScores:
        scores = self.strategies.evaluate(closes, volumes=volumes, rsi=rsi)
        self.last_scores = scores
        return scores

    def make_signal(
        self,
        cycle_id: str,
        symbol: str,
        closes: np.ndarray,
        probability: float,
        expected_return_pct: float,
        net_opportunity_pct: float,
        model_hash: str = "none",
        ttl_sec: float = 120.0,
        vol_20: float = 0.0,
        volumes: Optional[np.ndarray] = None,
        rsi: float = 50.0,
        use_strategy_overlay: bool = True,
    ) -> Signal:
        scores = self.evaluate_strategies(closes, volumes=volumes, rsi=rsi)
        regime = scores.regime

        # Blend ML/heuristic probability with strategy composite when overlay on
        prob = float(probability)
        exp_ret = float(expected_return_pct)
        if use_strategy_overlay:
            strat_prob = self.strategies.probability_from_scores(scores)
            # 55% strategy confluence / 45% external (ML or heuristic)
            prob = 0.45 * prob + 0.55 * strat_prob
            # Prefer strategy expected return when external is near-zero
            if abs(exp_ret) < 1e-9:
                exp_ret = self.strategies.expected_return_pct(scores)
            else:
                exp_ret = 0.5 * exp_ret + 0.5 * self.strategies.expected_return_pct(scores)

        # Regime-aware action (still no order authority)
        if regime == "UNKNOWN" or scores.regime_confidence < 0.35:
            action = "HOLD"
        elif prob >= 0.58 and net_opportunity_pct > 0 and regime in (
            "TREND_UP",
            "RANGE",
            "MEAN_REVERTING",
        ):
            # Mean-reversion BUY only if composite agrees (oversold bounce)
            if regime == "MEAN_REVERTING" and scores.composite < 0.05:
                action = "WAIT"
            else:
                action = "BUY"
        elif prob <= 0.42 and net_opportunity_pct > 0 and regime in (
            "TREND_DOWN",
            "RANGE",
            "MEAN_REVERTING",
        ):
            if regime == "MEAN_REVERTING" and scores.composite > -0.05:
                action = "WAIT"
            else:
                action = "SELL"
        elif regime == "VOLATILE" and abs(scores.composite) < 0.35:
            action = "HOLD"  # avoid chop unless strong breakout score
        else:
            action = "WAIT"

        ts = time.time()
        sid = uuid.uuid4().hex[:12]
        payload = {
            "signal_id": sid,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "action": action,
            "probability": round(prob, 6),
            "expected_return_pct": round(exp_ret, 6),
            "net_opportunity_pct": round(net_opportunity_pct, 6),
            "regime": regime,
            "feature_version": self.feature_version,
            "model_hash": model_hash,
            "strategy_composite": round(scores.composite, 6),
            "timestamp": ts,
        }
        sig = Signal(
            signal_id=sid,
            cycle_id=cycle_id,
            symbol=symbol,
            action=action,
            probability=float(prob),
            expected_return_pct=float(exp_ret),
            net_opportunity_pct=float(net_opportunity_pct),
            regime=regime,
            feature_version=self.feature_version,
            model_hash=model_hash,
            timestamp=ts,
            ttl_sec=ttl_sec,
            signal_hash=_hash_signal_payload(payload),
            strategy_composite=float(scores.composite),
            regime_confidence=float(scores.regime_confidence),
            strategy_reason=scores.reason,
        )
        self.last_signal = sig
        return sig

    def expected_returns_from_features(
        self, feature_map: Dict[str, np.ndarray]
    ) -> Dict[str, float]:
        """Heuristic expected return from features + optional strategy if closes provided elsewhere."""
        out: Dict[str, float] = {}
        for asset, vec in feature_map.items():
            if vec is None or len(vec) < 3:
                out[asset] = 0.0
                continue
            # ret_5 is index 2
            base = float(vec[2]) * 100.0 if len(vec) > 2 else 0.0
            # mom_12_1 if present (last feature in v2)
            if len(vec) >= 27:
                base = 0.6 * base + 0.4 * float(vec[26]) * 100.0
            out[asset] = base
        return out
