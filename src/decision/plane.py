"""DECISION PLANE - regime, signal, opportunity ranking. ML has no order authority."""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signal:
    signal_id: str
    cycle_id: str
    symbol: str
    action: str              # BUY / SELL / WAIT / HOLD
    probability: float
    expected_return_pct: float
    net_opportunity_pct: float
    regime: str
    feature_version: str
    model_hash: str
    timestamp: float
    ttl_sec: float
    signal_hash: str

    def expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) > self.timestamp + self.ttl_sec


def _hash_signal_payload(payload: Dict[str, Any]) -> str:
    raw = str(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class RegimeClassifier:
    """Rule-based regime — no ML required."""

    def classify(self, closes: np.ndarray, vol_20: float = 0.0) -> str:
        if closes is None or len(closes) < 30:
            return "UNKNOWN"
        c = closes.astype(np.float64)
        sma_fast = float(np.mean(c[-10:]))
        sma_slow = float(np.mean(c[-30:]))
        ret = float(np.log(c[-1] / c[-20])) if c[-20] > 0 else 0.0
        if vol_20 > 0.04:
            return "VOLATILE"
        if sma_fast > sma_slow * 1.005 and ret > 0.01:
            return "TREND_UP"
        if sma_fast < sma_slow * 0.995 and ret < -0.01:
            return "TREND_DOWN"
        return "RANGE"


class DecisionPlane:
    def __init__(self, feature_version: str = "v1"):
        self.feature_version = feature_version
        self.regime_clf = RegimeClassifier()
        self.last_signal: Optional[Signal] = None

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
    ) -> Signal:
        regime = self.regime_clf.classify(closes, vol_20)
        if regime == "UNKNOWN":
            action = "HOLD"
        elif probability >= 0.58 and net_opportunity_pct > 0 and regime in ("TREND_UP", "RANGE"):
            action = "BUY"
        elif probability <= 0.42 and net_opportunity_pct > 0 and regime in ("TREND_DOWN", "RANGE"):
            action = "SELL"
        else:
            action = "WAIT"

        ts = time.time()
        sid = uuid.uuid4().hex[:12]
        payload = {
            "signal_id": sid,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "action": action,
            "probability": round(probability, 6),
            "expected_return_pct": round(expected_return_pct, 6),
            "net_opportunity_pct": round(net_opportunity_pct, 6),
            "regime": regime,
            "feature_version": self.feature_version,
            "model_hash": model_hash,
            "timestamp": ts,
        }
        sig = Signal(
            signal_id=sid,
            cycle_id=cycle_id,
            symbol=symbol,
            action=action,
            probability=float(probability),
            expected_return_pct=float(expected_return_pct),
            net_opportunity_pct=float(net_opportunity_pct),
            regime=regime,
            feature_version=self.feature_version,
            model_hash=model_hash,
            timestamp=ts,
            ttl_sec=ttl_sec,
            signal_hash=_hash_signal_payload(payload),
        )
        self.last_signal = sig
        return sig

    def expected_returns_from_features(
        self, feature_map: Dict[str, np.ndarray]
    ) -> Dict[str, float]:
        """Heuristic expected return proxy from ret_5 / momentum features when ML absent."""
        out: Dict[str, float] = {}
        for asset, vec in feature_map.items():
            if vec is None or len(vec) < 3:
                out[asset] = 0.0
                continue
            # ret_5 is index 2 in FEATURE_NAMES
            out[asset] = float(vec[2]) * 100.0 if len(vec) > 2 else 0.0
        return out
