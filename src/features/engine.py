"""FEATURE PLANE - lightweight, float32, no lookahead, bounded dim."""
from __future__ import annotations

import hashlib
import logging
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

FEATURE_VERSION = "v2"
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

    def push(self, symbol: str, ts: float, o: float, h: float, low: float, c: float, v: float) -> None:
        if symbol not in self.buffers:
            self.buffers[symbol] = deque(maxlen=self.maxlen)
        buf = self.buffers[symbol]
        if buf and ts <= buf[-1][0]:
            return  # non-monotonic skip
        buf.append((ts, o, h, low, c, v))

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
        "macd_hist_norm",
        "stoch_rsi_k",
        "trend_strength",
        "vol_percentile_proxy",
        "hurst_proxy",
        "range_pos_20",
        "mom_12_1",
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

        # --- v2 richness (CPU-only, no lookahead) ---
        # MACD histogram normalized by price
        if len(c) >= 35:
            ema12 = float(c[-26])
            ema26 = float(c[-26])
            a12, a26 = 2.0 / 13.0, 2.0 / 27.0
            for px in c[-26:]:
                ema12 = a12 * float(px) + (1 - a12) * ema12
                ema26 = a26 * float(px) + (1 - a26) * ema26
            macd = ema12 - ema26
            feats.append(float(macd / (float(c[-1]) + 1e-12)))
        else:
            feats.append(0.0)

        # Stochastic RSI K (0-1)
        rsi_series = []
        if len(c) >= 30:
            for i in range(14, min(28, len(c))):
                window = c[i - 14 : i + 1]
                rsi_series.append(self._rsi(window, 14))
            if rsi_series:
                rmin, rmax = min(rsi_series), max(rsi_series)
                last_rsi = rsi_series[-1]
                stoch = (last_rsi - rmin) / (rmax - rmin + 1e-12)
                feats.append(float(stoch))
            else:
                feats.append(0.5)
        else:
            feats.append(0.5)

        # Trend strength |sma10-sma30|/price
        if len(c) >= 30:
            s10, s30 = float(np.mean(c[-10:])), float(np.mean(c[-30:]))
            feats.append(abs(s10 - s30) / (float(c[-1]) + 1e-12))
        else:
            feats.append(0.0)

        # Vol percentile proxy vs longer window
        if len(c) >= 60:
            r = np.diff(np.log(c.astype(np.float64) + 1e-12))
            v20 = float(np.std(r[-20:]))
            hist = [float(np.std(r[i - 20 : i])) for i in range(20, len(r) + 1)]
            if hist:
                feats.append(sum(1 for x in hist if x <= v20) / len(hist))
            else:
                feats.append(0.5)
        else:
            feats.append(0.5)

        # Hurst proxy
        if len(c) >= 45:
            r = np.diff(np.log(c.astype(np.float64) + 1e-12))
            vs = float(np.std(r[-10:])) + 1e-12
            vl = float(np.std(r[-40:])) + 1e-12
            feats.append(float(np.log(vs / vl) / np.log(10 / 40)))
        else:
            feats.append(0.0)

        # Range position 20
        if len(c) >= 20:
            hi, lo = float(np.max(c[-20:])), float(np.min(c[-20:]))
            feats.append((float(c[-1]) - lo) / (hi - lo + 1e-12))
        else:
            feats.append(0.5)

        # 12-1 momentum style (approx on bars): ret_12 - ret_1
        if len(c) > 12 and c[-13] > 0 and c[-2] > 0:
            r12 = float(np.log(c[-1] / c[-13]))
            r1 = float(np.log(c[-1] / c[-2]))
            feats.append(r12 - r1)
        else:
            feats.append(0.0)

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

    @classmethod
    def schema_hash(cls) -> str:
        names = ",".join(cls.FEATURE_NAMES)
        return hashlib.sha256(f"{FEATURE_VERSION}|{names}".encode()).hexdigest()[:32]
