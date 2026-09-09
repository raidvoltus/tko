"""Feature pipeline from OHLCV — causal only (no look-ahead)."""

from __future__ import annotations

from dataclasses import dataclass

from tko.core.types import OHLCV
from tko.strategy.btc import _ema, _rsi


@dataclass(frozen=True, slots=True)
class FeatureRow:
    timestamp_ms: int
    close: float
    ret_1: float
    ret_4: float
    volatility_8: float
    volume_z: float
    rsi_14: float
    ema_fast: float
    ema_slow: float
    ema_spread: float
    hour_utc: float
    dow_utc: float


FEATURE_NAMES: tuple[str, ...] = (
    "ret_1",
    "ret_4",
    "volatility_8",
    "volume_z",
    "rsi_14",
    "ema_fast_n",
    "ema_slow_n",
    "ema_spread",
    "hour_utc",
    "dow_utc",
)


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return var**0.5


def build_feature_matrix(
    candles: list[OHLCV],
    *,
    rsi_period: int = 14,
    ema_fast: int = 9,
    ema_slow: int = 21,
) -> list[FeatureRow]:
    """Build one feature row per bar using only past+current data at that bar."""
    if len(candles) < max(ema_slow, rsi_period, 10) + 2:
        return []
    closes = [c.close for c in candles]
    volumes = [float(c.volume if c.volume is not None else 0.0) for c in candles]
    ema_f = _ema(closes, ema_fast)
    ema_s = _ema(closes, ema_slow)
    rows: list[FeatureRow] = []
    start = max(ema_slow, rsi_period, 8) + 1
    for i in range(start, len(candles)):
        c = candles[i]
        rsi = _rsi(closes[: i + 1], rsi_period)
        if rsi is None:
            continue
        ret_1 = (closes[i] / closes[i - 1] - 1.0) if closes[i - 1] else 0.0
        ret_4 = (closes[i] / closes[i - 4] - 1.0) if i >= 4 and closes[i - 4] else 0.0
        window = closes[i - 8 : i + 1]
        rets = [
            (window[j] / window[j - 1] - 1.0) if window[j - 1] else 0.0
            for j in range(1, len(window))
        ]
        vol = _std(rets)
        vol_w = volumes[max(0, i - 19) : i + 1]
        v_mean = sum(vol_w) / len(vol_w) if vol_w else 0.0
        v_std = _std(vol_w) or 1.0
        volume_z = (volumes[i] - v_mean) / v_std
        ef = ema_f[i] / closes[i] if closes[i] else 0.0
        es = ema_s[i] / closes[i] if closes[i] else 0.0
        ts_sec = int(c.timestamp_ms) // 1000
        hour = (ts_sec % 86400) / 86400.0
        dow = ((ts_sec // 86400) % 7) / 7.0
        rows.append(
            FeatureRow(
                timestamp_ms=int(c.timestamp_ms),
                close=float(c.close),
                ret_1=ret_1,
                ret_4=ret_4,
                volatility_8=vol,
                volume_z=volume_z,
                rsi_14=rsi / 100.0,
                ema_fast=ef,
                ema_slow=es,
                ema_spread=ef - es,
                hour_utc=hour,
                dow_utc=dow,
            )
        )
    return rows


def rows_to_xy(
    rows: list[FeatureRow],
    labels: list[int],
) -> tuple[list[list[float]], list[int]]:
    if len(rows) != len(labels):
        raise ValueError("rows and labels length mismatch")
    x: list[list[float]] = []
    y: list[int] = []
    for r, lab in zip(rows, labels):
        if lab not in (-1, 0, 1):
            continue
        x.append(
            [
                r.ret_1,
                r.ret_4,
                r.volatility_8,
                r.volume_z,
                r.rsi_14,
                r.ema_fast,
                r.ema_slow,
                r.ema_spread,
                r.hour_utc,
                r.dow_utc,
            ]
        )
        y.append(lab)
    return x, y
