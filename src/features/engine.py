"""FEATURE PLANE - lightweight, float32, no lookahead, bounded dim."""
from __future__ import annotations

import hashlib
import logging
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

FEATURE_VERSION = "v1"
MAX_DIM = 64


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    if b == 0 or np.isnan(b) or np.isinf(b):
        return default
    return a / b


class CandleBuffer:
    """Ring buffer of OHLCV candles per symbol — bounded memory."""

    def __init__(self, maxlen: int = 300):
        self.maxlen = maxlen
        self.buffers: Dict[str, Deque[Tuple[float, float, float, float, float, float]]] = {}
        # (ts, o, h, l, c, v)

    def push(self, symbol: str, ts: float, o: float, h: float, l: float, c: float, v: float) -> None:
        if symbol not in self.buffers:
            self.buffers[symbol] = deque(maxlen=self.maxlen)
        buf = self.buffers[symbol]
        if buf and ts <= buf[-1][0]:
            return  # non-monotonic skip
        buf.append((ts, o, h, l, c, v))

    def closes(self, symbol: str) -> np.ndarray:
        buf = self.buffers.get(symbol)
        if not buf:
            return np.array([], dtype=np.float32)
        return np.array([x[4] for x in buf], dtype=np.float32)

    def volumes(self, symbol: str) -> np.ndarray:
        buf = self.buffers.get(symbol)
        if not buf:
            return np.array([], dtype=np.float32)
        return np.array([x[5] for x in buf], dtype=np.float32)

    def len(self, symbol: str) -> int:
        return len(self.buffers.get(symbol, ()))


class FeatureEngine:
    """Compute bounded float32 feature vector. No future data."""

    FEATURE_NAMES = [
        "ret_1", "ret_3", "ret_5", "ret_10", "ret_20",
        "vol_5", "vol_20",
        "sma_ratio_5", "sma_ratio_10", "sma_ratio_20",
        "ema_ratio_5", "ema_ratio_10",
        "rsi_14",
        "atr_ratio_14",
        "bb_width",
        "volume_ratio_20",
        "hour_sin", "hour_cos",
        "dow_sin", "dow_cos",
    ]

    def __init__(self, candle_buffer: Optional[CandleBuffer] = None):
        self.candles = candle_buffer or CandleBuffer()
        self.version = FEATURE_VERSION

    def compute(self, symbol: str, now_ts: Optional[float] = None) -> Tuple[np.ndarray, List[str]]:
        closes = self.candles.closes(symbol)
        vols = self.candles.volumes(symbol)
        names = list(self.FEATURE_NAMES)
        if len(closes) < 25:
            return np.zeros(len(names), dtype=np.float32), names

        c = closes
        feats: List[float] = []

        # returns
        for lag in (1, 3, 5, 10, 20):
            if len(c) > lag and c[-lag - 1] != 0:
                feats.append(float(np.log(c[-1] / c[-lag - 1])))
            else:
                feats.append(0.0)

        # realized vol
        for w in (5, 20):
            if len(c) > w:
                r = np.diff(np.log(c[-(w + 1):] + 1e-12))
                feats.append(float(np.std(r)))
            else:
                feats.append(0.0)

        # SMA ratios
        for w in (5, 10, 20):
            sma = float(np.mean(c[-w:]))
            feats.append(_safe_div(float(c[-1]), sma, 1.0) - 1.0)

        # EMA ratios (simple recursive approx from window)
        for w in (5, 10):
            alpha = 2.0 / (w + 1)
            ema = float(c[-w])
            for px in c[-w + 1:]:
                ema = alpha * float(px) + (1 - alpha) * ema
            feats.append(_safe_div(float(c[-1]), ema, 1.0) - 1.0)

        # RSI 14
        feats.append(self._rsi(c, 14))

        # ATR ratio (use high-low proxy from close range)
        if len(c) > 15:
            tr = np.abs(np.diff(c[-15:]))
            atr = float(np.mean(tr))
            feats.append(_safe_div(atr, float(c[-1]), 0.0))
        else:
            feats.append(0.0)

        # Bollinger width
        if len(c) >= 20:
            m = float(np.mean(c[-20:]))
            s = float(np.std(c[-20:]))
            feats.append(_safe_div(2 * s, m, 0.0))
        else:
            feats.append(0.0)

        # volume ratio
        if len(vols) >= 20:
            feats.append(_safe_div(float(vols[-1]), float(np.mean(vols[-20:])), 1.0) - 1.0)
        else:
            feats.append(0.0)

        # calendar
        import time
        ts = now_ts or time.time()
        lt = time.localtime(ts)
        hour = lt.tm_hour + lt.tm_min / 60.0
        dow = lt.tm_wday
        feats.append(float(np.sin(2 * np.pi * hour / 24)))
        feats.append(float(np.cos(2 * np.pi * hour / 24)))
        feats.append(float(np.sin(2 * np.pi * dow / 7)))
        feats.append(float(np.cos(2 * np.pi * dow / 7)))

        arr = np.array(feats[:MAX_DIM], dtype=np.float32)
        # pad/truncate
        if len(arr) < len(names):
            arr = np.pad(arr, (0, len(names) - len(arr)))
        arr = arr[: len(names)]
        return arr, names

    @staticmethod
    def _rsi(closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        d = np.diff(closes[-(period + 1):].astype(np.float64))
        gains = np.where(d > 0, d, 0.0)
        losses = np.where(d < 0, -d, 0.0)
        ag = float(np.mean(gains))
        al = float(np.mean(losses))
        if al == 0:
            return 100.0
        rs = ag / al
        return float(100 - (100 / (1 + rs)))

    def features_hash(self, vec: np.ndarray) -> str:
        return hashlib.sha256(vec.tobytes()).hexdigest()[:16]
