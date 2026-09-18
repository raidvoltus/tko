"""
Strategy richness layer — composite confluence scores.

Research-aligned (regime-aware momentum vs mean-reversion, multi-indicator confluence):
- Momentum / trend continuation
- Mean reversion (fade extremes in RANGE)
- Breakout / volatility expansion
- Composite score in [-1, +1] with regime gating

Does NOT submit orders. Output feeds DecisionPlane only.
Risk Engine remains sole execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class StrategyScores:
    momentum: float
    mean_reversion: float
    breakout: float
    trend_score: float
    regime_fit: float
    volume_confirmation: float
    volatility_confirmation: float
    structure_confirmation: float
    strategy_confidence: float
    composite: float
    regime: str
    regime_confidence: float
    allowed: Tuple[str, ...]
    reason: str


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, x)))


def _tanh(x: float) -> float:
    return float(np.tanh(x))


class RegimeDetector:
    """
    Multi-signal regime classification with hysteresis.
    Uses trend structure, vol state, range position, momentum persistence.
    hurst_proxy is a variance-ratio-like proxy — NOT a true Hurst exponent.
    """

    def __init__(self, persist_bars: int = 3):
        self.persist_bars = persist_bars
        self._last_regime = "UNKNOWN"
        self._streak = 0

    def classify(
        self,
        closes: np.ndarray,
        high: Optional[np.ndarray] = None,
        low: Optional[np.ndarray] = None,
    ) -> Tuple[str, float, Tuple[str, ...]]:
        if closes is None or len(closes) < 40:
            return "UNKNOWN", 0.0, ()

        c = closes.astype(np.float64)
        ret = np.diff(np.log(c + 1e-12))
        vol20 = float(np.std(ret[-20:])) if len(ret) >= 20 else 0.0
        vol5 = float(np.std(ret[-5:])) if len(ret) >= 5 else 0.0

        sma10 = float(np.mean(c[-10:]))
        sma30 = float(np.mean(c[-30:]))
        sma50 = float(np.mean(c[-min(50, len(c)) :]))
        ret20 = float(np.log(c[-1] / c[-21])) if len(c) > 21 and c[-21] > 0 else 0.0

        # BB width normalized
        if len(c) >= 20:
            m = float(np.mean(c[-20:]))
            s = float(np.std(c[-20:]))
            bb_w = (2 * s) / m if m > 0 else 0.0
        else:
            bb_w = 0.0

        # Hurst proxy: log(vol_short/vol_long)/log(T_s/T_l)
        vol_s = float(np.std(ret[-10:])) if len(ret) >= 10 else 0.0
        vol_l = float(np.std(ret[-40:])) if len(ret) >= 40 else vol_s
        hurst_proxy = 0.0
        if vol_l > 1e-12:
            hurst_proxy = float(np.log((vol_s + 1e-12) / vol_l) / np.log(10 / 40))

        # ADX-like trend strength: |sma_fast-sma_slow|/price
        trend_str = abs(sma10 - sma30) / (c[-1] + 1e-12)

        conf = 0.5
        if vol20 > 0.045 or bb_w > 0.08:
            regime = "VOLATILE"
            allowed = ("breakout",)  # only careful breakout / avoid mean-rev
            conf = min(0.95, 0.55 + vol20 * 5)
        elif sma10 > sma30 * 1.004 and sma30 >= sma50 * 0.998 and ret20 > 0.008:
            regime = "TREND_UP"
            allowed = ("momentum", "breakout")
            conf = min(0.95, 0.55 + trend_str * 20 + max(0.0, ret20) * 5)
        elif sma10 < sma30 * 0.996 and sma30 <= sma50 * 1.002 and ret20 < -0.008:
            regime = "TREND_DOWN"
            allowed = ("momentum", "breakout")
            conf = min(0.95, 0.55 + trend_str * 20 + max(0.0, -ret20) * 5)
        elif hurst_proxy < -0.05 and bb_w < 0.04 and vol20 < 0.025:
            regime = "MEAN_REVERTING"
            allowed = ("mean_reversion",)
            conf = min(0.9, 0.5 + abs(hurst_proxy) * 2)
        else:
            regime = "RANGE"
            allowed = ("mean_reversion", "momentum")
            conf = 0.45 + (0.1 if vol5 < vol20 else 0.0)

        # Hysteresis: require persistence before switching (except UNKNOWN/VOLATILE)
        if regime == self._last_regime:
            self._streak += 1
        else:
            if regime in ("VOLATILE", "UNKNOWN") or self._last_regime in ("UNKNOWN",):
                self._last_regime = regime
                self._streak = 1
            elif self._streak >= self.persist_bars:
                self._last_regime = regime
                self._streak = 1
            else:
                # keep previous regime until persistence met
                regime = self._last_regime
                self._streak += 1
                conf = max(0.3, conf * 0.85)

        return regime, conf, allowed


class StrategyEngine:
    """
    Per-strategy scores in [-1, +1] and regime-gated composite.
    Positive = bullish pressure, negative = bearish.
    """

    # Weights when strategy is allowed
    W = {
        "momentum": 0.40,
        "mean_reversion": 0.35,
        "breakout": 0.25,
    }

    def __init__(self):
        self.regime = RegimeDetector()

    def score_momentum(self, closes: np.ndarray, rsi: float = 50.0) -> float:
        if closes is None or len(closes) < 30:
            return 0.0
        c = closes.astype(np.float64)
        # Multi-horizon momentum (1, 5, 10, 20)
        scores = []
        for lag, w in ((1, 0.15), (5, 0.25), (10, 0.30), (20, 0.30)):
            if len(c) > lag and c[-lag - 1] > 0:
                r = float(np.log(c[-1] / c[-lag - 1]))
                scores.append(w * _tanh(r * 25))
        # EMA structure
        ema_f = self._ema(c, 8)
        ema_s = self._ema(c, 21)
        struct = _tanh((ema_f - ema_s) / (c[-1] + 1e-12) * 50)
        # RSI tilt (momentum prefers mid-high RSI in trend, not extreme)
        rsi_t = _clip((rsi - 50.0) / 50.0)
        return _clip(sum(scores) * 0.7 + struct * 0.2 + rsi_t * 0.1)

    def score_mean_reversion(self, closes: np.ndarray, rsi: float = 50.0) -> float:
        if closes is None or len(closes) < 25:
            return 0.0
        c = closes.astype(np.float64)
        m = float(np.mean(c[-20:]))
        s = float(np.std(c[-20:])) + 1e-12
        z = (float(c[-1]) - m) / s
        # Fade extremes: positive score when oversold (expect bounce)
        z_score = _clip(-z / 2.0)
        rsi_fade = 0.0
        if rsi < 30:
            rsi_fade = (30 - rsi) / 30.0
        elif rsi > 70:
            rsi_fade = -(rsi - 70) / 30.0
        return _clip(0.65 * z_score + 0.35 * rsi_fade)

    def score_breakout(self, closes: np.ndarray, volumes: Optional[np.ndarray] = None) -> float:
        if closes is None or len(closes) < 30:
            return 0.0
        c = closes.astype(np.float64)
        hi = float(np.max(c[-20:-1])) if len(c) > 21 else float(c[-1])
        lo = float(np.min(c[-20:-1])) if len(c) > 21 else float(c[-1])
        last = float(c[-1])
        rng = hi - lo + 1e-12
        # Break above range → +, below → -
        pos = (last - lo) / rng
        brk = 0.0
        if last > hi:
            brk = _tanh((last - hi) / rng * 3)
        elif last < lo:
            brk = -_tanh((lo - last) / rng * 3)
        else:
            brk = (pos - 0.5) * 0.3
        vol_boost = 1.0
        if volumes is not None and len(volumes) >= 20:
            v = volumes.astype(np.float64)
            vr = float(v[-1] / (np.mean(v[-20:]) + 1e-12))
            vol_boost = float(np.clip(vr / 1.5, 0.5, 1.5))
        return _clip(brk * vol_boost)

    def evaluate(
        self,
        closes: np.ndarray,
        volumes: Optional[np.ndarray] = None,
        rsi: float = 50.0,
    ) -> StrategyScores:
        regime, conf, allowed = self.regime.classify(closes)
        mom = self.score_momentum(closes, rsi)
        mr = self.score_mean_reversion(closes, rsi)
        brk = self.score_breakout(closes, volumes)

        parts: List[Tuple[str, float, float]] = []
        if "momentum" in allowed:
            parts.append(("momentum", mom, self.W["momentum"]))
        if "mean_reversion" in allowed:
            parts.append(("mean_reversion", mr, self.W["mean_reversion"]))
        if "breakout" in allowed:
            parts.append(("breakout", brk, self.W["breakout"]))

        if not parts:
            composite = 0.0
            reason = f"regime={regime} no_allowed_strategies"
        else:
            wsum = sum(w for _, _, w in parts) or 1.0
            composite = sum(s * w for _, s, w in parts) / wsum
            # Scale by regime confidence
            composite = _clip(composite * (0.5 + 0.5 * conf))
            reason = "allowed=" + ",".join(a for a, _, _ in parts)

        # Confirmation channels
        trend_score = mom
        structure = abs(mom) * 0.5 + abs(brk) * 0.5
        vol_conf = 1.0 if regime == "VOLATILE" else (0.7 if regime in ("TREND_UP", "TREND_DOWN") else 0.5)
        # volume_confirmation passed via breakout already scaled; use |brk| as proxy when no vol
        vol_confirm = min(1.0, abs(brk) * 1.2)
        regime_fit = conf
        strategy_confidence = min(1.0, (abs(composite) + conf) / 2.0)

        return StrategyScores(
            momentum=mom,
            mean_reversion=mr,
            breakout=brk,
            trend_score=trend_score,
            regime_fit=regime_fit,
            volume_confirmation=vol_confirm,
            volatility_confirmation=vol_conf,
            structure_confirmation=min(1.0, structure),
            strategy_confidence=strategy_confidence,
            composite=composite,
            regime=regime,
            regime_confidence=conf,
            allowed=allowed,
            reason=reason,
        )

    def expected_return_pct(self, scores: StrategyScores, scale: float = 2.5) -> float:
        """Map composite [-1,1] to expected return percent heuristic (not a forecast guarantee)."""
        return float(scores.composite * scale)

    def probability_from_scores(self, scores: StrategyScores) -> float:
        """Map composite to [0,1] probability-like score for DecisionPlane."""
        return float(0.5 + 0.5 * scores.composite)

    @staticmethod
    def _ema(c: np.ndarray, w: int) -> float:
        if len(c) < w:
            return float(c[-1])
        alpha = 2.0 / (w + 1)
        ema = float(c[-w])
        for px in c[-w + 1 :]:
            ema = alpha * float(px) + (1 - alpha) * ema
        return ema
