"""Simple BTC potential analysis (RSI + EMA)."""

from __future__ import annotations

from dataclasses import dataclass

from tko.core.config import Settings
from tko.core.types import OHLCV, Signal


@dataclass(frozen=True, slots=True)
class TradeDecision:
    signal: Signal
    reason: str
    strength: float  # 0..1
    last_price: float


def _ema(values: list[float], period: int) -> list[float]:
    if not values or period < 1:
        return []
    k = 2 / (period + 1)
    out: list[float] = []
    ema = values[0]
    for v in values:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def _rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class BtcAnalyzer:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def analyze(self, candles: list[OHLCV]) -> TradeDecision:
        if len(candles) < max(self.s.ema_slow, self.s.rsi_period) + 2:
            return TradeDecision(Signal.HOLD, "insufficient candles", 0.0, 0.0)

        closes = [c.close for c in candles]
        last = closes[-1]
        rsi = _rsi(closes, self.s.rsi_period)
        ema_fast = _ema(closes, self.s.ema_fast)
        ema_slow = _ema(closes, self.s.ema_slow)

        if rsi is None or not ema_fast or not ema_slow:
            return TradeDecision(Signal.HOLD, "indicator unavailable", 0.0, last)

        f, s = ema_fast[-1], ema_slow[-1]
        f_prev, s_prev = ema_fast[-2], ema_slow[-2]

        cross_up = f_prev <= s_prev and f > s
        cross_down = f_prev >= s_prev and f < s
        oversold = rsi <= self.s.rsi_oversold
        overbought = rsi >= self.s.rsi_overbought

        if cross_up and rsi < 60:
            strength = min(1.0, (60 - rsi) / 30 + 0.3)
            return TradeDecision(
                Signal.BUY,
                f"EMA cross up + RSI={rsi:.1f}",
                strength,
                last,
            )
        if oversold and f >= s:
            return TradeDecision(
                Signal.BUY,
                f"RSI oversold ({rsi:.1f}) + EMA bullish",
                0.6,
                last,
            )
        if cross_down or overbought:
            return TradeDecision(
                Signal.SELL,
                f"EMA cross down or RSI overbought ({rsi:.1f})",
                0.7 if overbought else 0.55,
                last,
            )
        return TradeDecision(Signal.HOLD, f"RSI={rsi:.1f} EMA f={f:.2f}/s={s:.2f}", 0.0, last)
