"""
Point-in-time (PIT) dataset schema.

Rules:
- Row t may only use information available at or before t (no lookahead).
- Labels for horizon H use future returns from t→t+H but are stored as
  targets for supervised training; inference at t never reads label_t.
- Feature columns are frozen by schema_hash; adding columns changes version.

This module does not place orders and does not call Risk/Execution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


PIT_SCHEMA_VERSION = "pit-v1"


@dataclass(frozen=True)
class PITColumnSpec:
    name: str
    role: str  # feature | label | meta | mask
    description: str = ""


# Canonical column roles for TKO empirical evaluation
DEFAULT_COLUMNS: Tuple[PITColumnSpec, ...] = (
    PITColumnSpec("ts", "meta", "Unix timestamp of bar close (inclusive)"),
    PITColumnSpec("symbol", "meta", "Trading symbol"),
    PITColumnSpec("open", "meta", "OHLC open"),
    PITColumnSpec("high", "meta", "OHLC high"),
    PITColumnSpec("low", "meta", "OHLC low"),
    PITColumnSpec("close", "meta", "OHLC close — available at bar close"),
    PITColumnSpec("volume", "meta", "Bar volume"),
    PITColumnSpec("ret_1", "feature", "log(close_t/close_{t-1})"),
    PITColumnSpec("ret_5", "feature", "log(close_t/close_{t-5})"),
    PITColumnSpec("vol_20", "feature", "realized vol 20 bars"),
    PITColumnSpec("rsi_14", "feature", "RSI 14"),
    PITColumnSpec("fwd_ret_1", "label", "log(close_{t+1}/close_t) — train target only"),
    PITColumnSpec("fwd_ret_5", "label", "log(close_{t+5}/close_t) — train target only"),
    PITColumnSpec("is_train_ok", "mask", "1 if row eligible for train (full history)"),
)


def schema_hash(columns: Sequence[PITColumnSpec] = DEFAULT_COLUMNS) -> str:
    payload = [{"name": c.name, "role": c.role} for c in columns]
    raw = json.dumps({"version": PIT_SCHEMA_VERSION, "cols": payload}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class PITDataset:
    """
    Columnar PIT store. Arrays aligned by row index 0..n-1 chronological.
    """

    symbol: str
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    features: Dict[str, np.ndarray] = field(default_factory=dict)
    labels: Dict[str, np.ndarray] = field(default_factory=dict)
    schema_version: str = PIT_SCHEMA_VERSION
    feature_schema_hash: str = field(default_factory=lambda: schema_hash())

    def __post_init__(self) -> None:
        n = len(self.ts)
        for name, arr in list(self.features.items()) + list(self.labels.items()):
            if len(arr) != n:
                raise ValueError(f"length mismatch {name}: {len(arr)} != {n}")
        if not (len(self.close) == n == len(self.open) == len(self.high) == len(self.low) == len(self.volume)):
            raise ValueError("OHLCV length mismatch")

    @property
    def n(self) -> int:
        return int(len(self.ts))

    def slice(self, start: int, end: int) -> "PITDataset":
        """Inclusive start, exclusive end — used for walk-forward windows."""
        return PITDataset(
            symbol=self.symbol,
            ts=self.ts[start:end],
            open=self.open[start:end],
            high=self.high[start:end],
            low=self.low[start:end],
            close=self.close[start:end],
            volume=self.volume[start:end],
            features={k: v[start:end] for k, v in self.features.items()},
            labels={k: v[start:end] for k, v in self.labels.items()},
            schema_version=self.schema_version,
            feature_schema_hash=self.feature_schema_hash,
        )

    def feature_matrix(self, names: Optional[Sequence[str]] = None) -> np.ndarray:
        keys = list(names) if names is not None else sorted(self.features.keys())
        if not keys:
            return np.zeros((self.n, 0), dtype=np.float64)
        cols = [np.asarray(self.features[k], dtype=np.float64) for k in keys]
        return np.column_stack(cols)

    def assert_no_lookahead_labels_in_features(self) -> None:
        """Hard check: feature keys must not start with fwd_ or future_."""
        for k in self.features:
            kl = k.lower()
            if kl.startswith("fwd_") or kl.startswith("future_") or "lead" in kl:
                raise AssertionError(f"lookahead feature name forbidden: {k}")

    def to_meta(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "n": self.n,
            "schema_version": self.schema_version,
            "feature_schema_hash": self.feature_schema_hash,
            "feature_names": sorted(self.features.keys()),
            "label_names": sorted(self.labels.keys()),
            "ts_start": float(self.ts[0]) if self.n else None,
            "ts_end": float(self.ts[-1]) if self.n else None,
        }


def build_pit_from_closes(
    symbol: str,
    closes: Sequence[float],
    volumes: Optional[Sequence[float]] = None,
    ts0: float = 1_700_000_000.0,
    bar_sec: float = 60.0,
) -> PITDataset:
    """
    Construct a minimal PIT dataset from a close series (synthetic or historical).
    Features use only past closes; labels are forward returns (train targets only).
    """
    c = np.asarray(closes, dtype=np.float64)
    n = len(c)
    if n < 30:
        raise ValueError("need at least 30 bars")
    vol = np.asarray(volumes, dtype=np.float64) if volumes is not None else np.ones(n)
    if len(vol) != n:
        raise ValueError("volume length mismatch")
    ts = ts0 + np.arange(n, dtype=np.float64) * bar_sec
    # OHLC proxy from close (evaluation harness; real data should supply true OHLC)
    o = np.roll(c, 1)
    o[0] = c[0]
    h = np.maximum(o, c)
    low = np.minimum(o, c)

    ret_1 = np.zeros(n)
    ret_1[1:] = np.log(c[1:] / c[:-1])
    ret_5 = np.zeros(n)
    ret_5[5:] = np.log(c[5:] / c[:-5])
    vol_20 = np.zeros(n)
    for i in range(20, n):
        vol_20[i] = float(np.std(ret_1[i - 19 : i + 1]))
    rsi = np.full(n, 50.0)
    for i in range(15, n):
        d = np.diff(c[i - 14 : i + 1])
        gains = np.where(d > 0, d, 0.0)
        losses = np.where(d < 0, -d, 0.0)
        ag, al = float(np.mean(gains)), float(np.mean(losses))
        rsi[i] = 100.0 if al == 0 else 100 - (100 / (1 + ag / al))

    fwd_1 = np.full(n, np.nan)
    fwd_1[:-1] = np.log(c[1:] / c[:-1])
    fwd_5 = np.full(n, np.nan)
    fwd_5[:-5] = np.log(c[5:] / c[:-5])

    return PITDataset(
        symbol=symbol,
        ts=ts,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=vol,
        features={
            "ret_1": ret_1,
            "ret_5": ret_5,
            "vol_20": vol_20,
            "rsi_14": rsi,
        },
        labels={
            "fwd_ret_1": fwd_1,
            "fwd_ret_5": fwd_5,
        },
        feature_schema_hash=schema_hash(),
    )


def assert_feature_label_alignment(ds: PITDataset, horizon: int = 1) -> None:
    """
    At index t, feature uses close[t]; label fwd_ret_h uses close[t+h]/close[t].
    Verify last `horizon` labels are NaN (no future bar).
    """
    key = f"fwd_ret_{horizon}"
    if key not in ds.labels:
        raise KeyError(key)
    lab = ds.labels[key]
    if not np.all(np.isnan(lab[-horizon:])):
        raise AssertionError(f"{key} must be NaN on last {horizon} rows (no future)")
    ds.assert_no_lookahead_labels_in_features()
