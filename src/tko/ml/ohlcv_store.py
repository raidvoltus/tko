"""Append-only OHLCV store (UTC timestamps). No look-ahead."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from tko.core.types import OHLCV

logger = logging.getLogger(__name__)

_HEADER = ("timestamp_ms", "open", "high", "low", "close", "volume")


class OhlcvStore:
    """CSV store per symbol+timeframe. Timestamps are exchange UTC ms."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "_").replace(":", "_")
        return self.root / f"{safe}_{timeframe}.csv"

    def append(self, symbol: str, timeframe: str, candles: list[OHLCV]) -> int:
        if not candles:
            return 0
        path = self._path(symbol, timeframe)
        existing_ts: set[int] = set()
        if path.exists():
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        existing_ts.add(int(row["timestamp_ms"]))
                    except (KeyError, ValueError):
                        continue
        new_rows = [c for c in candles if int(c.timestamp_ms) not in existing_ts]
        if not new_rows:
            return 0
        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(_HEADER)
            for c in sorted(new_rows, key=lambda x: x.timestamp_ms):
                w.writerow(
                    [
                        int(c.timestamp_ms),
                        float(c.open),
                        float(c.high),
                        float(c.low),
                        float(c.close),
                        float(c.volume if c.volume is not None else 0.0),
                    ]
                )
        logger.info(
            "event=ohlcv_appended symbol=%s tf=%s n=%s path=%s",
            symbol,
            timeframe,
            len(new_rows),
            path.name,
        )
        return len(new_rows)

    def load(self, symbol: str, timeframe: str) -> list[OHLCV]:
        path = self._path(symbol, timeframe)
        if not path.exists():
            return []
        out: list[OHLCV] = []
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    out.append(
                        OHLCV(
                            timestamp_ms=int(row["timestamp_ms"]),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row.get("volume") or 0.0),
                        )
                    )
                except (KeyError, ValueError, TypeError):
                    continue
        out.sort(key=lambda c: c.timestamp_ms)
        return out
