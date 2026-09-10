"""ML signal filter — gates rule-based signals; never sends orders alone."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tko.core.types import OHLCV, Signal
from tko.ml.features import build_feature_matrix
from tko.ml.models import load_model, sklearn_available
from tko.strategy.btc import TradeDecision

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FilterDecision:
    allow: bool
    rule_signal: Signal
    ml_direction: int
    confidence: float
    reason: str


class MlSignalFilter:
    """Optional filter: rule BUY only if model agrees with confidence.

    Fail-closed: missing model/sklearn → block BUY; SELL still allowed.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        model_path: Path | None = None,
        min_confidence: float = 0.55,
        model: Any | None = None,
        governor: Any | None = None,
        pool: Any | None = None,
        ensemble: Any | None = None,
    ) -> None:
        self.enabled = enabled
        self.min_confidence = min_confidence
        self._model = model
        self._model_path = Path(model_path) if model_path else None
        # Stage 5.1 optional hooks (default None = no change to Stage 5 behaviour)
        self.governor = governor
        self.pool = pool
        self.ensemble = ensemble
        if self.enabled and self._model is None and self._model_path and self._model_path.exists():
            try:
                self._model = load_model(self._model_path)
            except Exception as exc:
                logger.error("event=ml_model_load_failed err=%s", exc)
                self._model = None

    def filter(
        self,
        rule: TradeDecision,
        candles: list[OHLCV],
        *,
        rsi_period: int = 14,
        ema_fast: int = 9,
        ema_slow: int = 21,
    ) -> FilterDecision:
        if not self.enabled:
            return FilterDecision(True, rule.signal, 0, 0.0, "ml_filter_disabled")
        # Stage 5.1: SAFE_EXIT suppresses BUY only; SELL remains free
        if self.governor is not None and getattr(self.governor, "should_hold_only", lambda: False)():
            if rule.signal == Signal.SELL:
                return FilterDecision(True, rule.signal, 0, 0.0, "governor_safe_exit_allow_sell")
            return FilterDecision(False, rule.signal, 0, 0.0, "governor_safe_exit")
        if rule.signal == Signal.HOLD:
            return FilterDecision(True, rule.signal, 0, 0.0, "hold_passthrough")
        if not sklearn_available() or self._model is None:
            if rule.signal == Signal.SELL:
                return FilterDecision(True, rule.signal, 0, 0.0, "ml_unavailable_allow_sell")
            return FilterDecision(False, rule.signal, 0, 0.0, "ml_unavailable_block_buy")
        rows = build_feature_matrix(
            candles, rsi_period=rsi_period, ema_fast=ema_fast, ema_slow=ema_slow
        )
        if not rows:
            return FilterDecision(False, rule.signal, 0, 0.0, "no_features")
        r = rows[-1]
        x = [[
            r.ret_1, r.ret_4, r.volatility_8, r.volume_z, r.rsi_14,
            r.ema_fast, r.ema_slow, r.ema_spread, r.hour_utc, r.dow_utc,
        ]]
        try:
            pred = int(self._model.predict(x)[0])
            conf = 0.5
            if hasattr(self._model, "predict_proba"):
                proba = self._model.predict_proba(x)[0]
                conf = float(max(proba))
        except Exception as exc:
            logger.warning("event=ml_predict_failed err=%s", exc)
            if rule.signal == Signal.SELL:
                return FilterDecision(True, rule.signal, 0, 0.0, "ml_predict_fail_allow_sell")
            return FilterDecision(False, rule.signal, 0, 0.0, "ml_predict_fail_block_buy")

        if rule.signal == Signal.BUY:
            allow = pred == 1 and conf >= self.min_confidence
            reason = "ml_agree_buy" if allow else f"ml_block_buy pred={pred} conf={conf:.2f}"
            return FilterDecision(allow, rule.signal, pred, conf, reason)
        if rule.signal == Signal.SELL:
            return FilterDecision(True, rule.signal, pred, conf, "sell_always_allowed")
        return FilterDecision(True, rule.signal, pred, conf, "passthrough")
