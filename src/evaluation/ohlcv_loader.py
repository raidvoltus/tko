"""
Load OHLCV into PITDataset from CSV (point-in-time safe if rows are append-only closes).

Expected CSV columns (case-insensitive):
  timestamp|ts|time, open, high, low, close, volume
  optional: symbol

No network fetch of credentials. Operator supplies files.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional, Union

import numpy as np

from src.evaluation.pit_dataset import PITDataset, build_pit_from_closes


def load_ohlcv_csv(
    path: Union[str, Path],
    symbol: str = "UNKNOWN",
    ts_unit: str = "s",
) -> PITDataset:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    rows: List[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("empty CSV")
        fields = {name.lower().strip(): name for name in reader.fieldnames}

        def col(*candidates: str) -> Optional[str]:
            for c in candidates:
                if c in fields:
                    return fields[c]
            return None

        c_ts = col("timestamp", "ts", "time", "date")
        c_o = col("open", "o")
        c_h = col("high", "h")
        c_l = col("low", "l")
        c_c = col("close", "c")
        c_v = col("volume", "vol", "v")
        c_sym = col("symbol", "pair")
        if not c_c:
            raise ValueError("CSV must include close")
        for row in reader:
            try:
                close = float(row[c_c])
            except (TypeError, ValueError, KeyError):
                continue
            o = float(row[c_o]) if c_o and row.get(c_o) not in (None, "") else close
            h = float(row[c_h]) if c_h and row.get(c_h) not in (None, "") else close
            low = float(row[c_l]) if c_l and row.get(c_l) not in (None, "") else close
            v = float(row[c_v]) if c_v and row.get(c_v) not in (None, "") else 0.0
            if c_ts and row.get(c_ts) not in (None, ""):
                raw = row[c_ts]
                try:
                    ts = float(raw)
                    if ts_unit == "ms" or ts > 1e12:
                        ts = ts / 1000.0
                except ValueError:
                    # skip non-numeric timestamps for harness simplicity
                    ts = float(len(rows))
            else:
                ts = float(len(rows))
            sym = row[c_sym] if c_sym and row.get(c_sym) else symbol
            rows.append({"ts": ts, "o": o, "h": h, "l": low, "c": close, "v": v, "symbol": sym})
    if len(rows) < 30:
        raise ValueError("need >= 30 valid rows")
    rows.sort(key=lambda r: r["ts"])
    closes = [r["c"] for r in rows]
    vols = [r["v"] for r in rows]
    sym = rows[0]["symbol"] or symbol
    ds = build_pit_from_closes(sym, closes, volumes=vols, ts0=float(rows[0]["ts"]))
    # overwrite OHLC with true values when provided
    ds.open = np.asarray([r["o"] for r in rows], dtype=np.float64)
    ds.high = np.asarray([r["h"] for r in rows], dtype=np.float64)
    ds.low = np.asarray([r["l"] for r in rows], dtype=np.float64)
    ds.close = np.asarray([r["c"] for r in rows], dtype=np.float64)
    ds.volume = np.asarray([r["v"] for r in rows], dtype=np.float64)
    ds.ts = np.asarray([r["ts"] for r in rows], dtype=np.float64)
    return ds
