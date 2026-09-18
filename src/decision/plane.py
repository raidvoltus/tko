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
from src.decision.ensemble import Ensemble
from src.decision.edge import cost_adjusted_edge, strategy_score_to_gross_edge
from src.decision.governor import Governor

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
        self.ensemble = Ensemble(strategy_weight=0.7, model_weight=0.3)
        self.governor = Governor()
        self.last_signal: Optional[Signal] = None
        self.last_scores: Optional[StrategyScores] = None
        self.last_governor = None

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

        # Ensemble fuse: strategy composite + optional model score (evidence only)
        # External `probability` treated as model_score in [-1,1] or [0,1] → map to [-1,1]
        model_score = None
        if model_hash and model_hash not in ("none", "heuristic"):
            # map [0,1]-ish probability to [-1,1] score
            model_score = float(probability) * 2.0 - 1.0
        ens = self.ensemble.fuse(scores.composite, model_score)
        # Cost-adjusted edge from uncalibrated gross heuristic
        gross = strategy_score_to_gross_edge(ens.confluence_score)
        edge = cost_adjusted_edge(gross, source="heuristic_uncalibrated", calibrated=False)

        prob = float(probability)
        exp_ret = float(expected_return_pct)
        if use_strategy_overlay:
            # Prefer confluence score as primary evidence; do not call it calibrated probability
            strat_score_01 = 0.5 + 0.5 * ens.confluence_score
            prob = 0.45 * prob + 0.55 * strat_score_01
            if abs(exp_ret) < 1e-9:
                exp_ret = edge.net_edge_pct
            else:
                exp_ret = 0.5 * exp_ret + 0.5 * edge.net_edge_pct

        # Governor policy (NO order authority). Risk remains separate gate at execution.
        gov = self.governor.decide(
            scores,
            model_score=model_score,
            edge=edge,
            data_valid=regime != "UNKNOWN",
            stale=False,
            risk_blocked=False,
            min_net_edge_pct=0.0,
        )
        self.last_governor = gov
        action = gov.action
        # Align WAIT/NO_TRADE/HOLD with net opportunity floor from rotation
        if action in ("BUY", "SELL") and net_opportunity_pct <= 0:
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
