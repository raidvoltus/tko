"""OHLCV data validation — fail-closed quality report (no future fill)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tko.core.types import OHLCV


@dataclass
class DataQualityReport:
    rows: int = 0
    usable_rows: int = 0
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    timeframe: str = ""
    missing_intervals: int = 0
    duplicates: int = 0
    invalid_rows: int = 0
    zero_volume: int = 0
    nan_or_inf: int = 0
    bad_ohlc: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "usable_rows": self.usable_rows,
            "start_time_ms": self.start_time_ms,
            "end_time_ms": self.end_time_ms,
            "timeframe": self.timeframe,
            "missing_intervals": self.missing_intervals,
            "duplicates": self.duplicates,
            "invalid_rows": self.invalid_rows,
            "zero_volume": self.zero_volume,
            "nan_or_inf": self.nan_or_inf,
            "bad_ohlc": self.bad_ohlc,
            "notes": list(self.notes),
        }


def _tf_ms(timeframe: str) -> int:
    s = (timeframe or "15m").strip().lower()
    if s.endswith("m"):
        return int(float(s[:-1]) * 60_000)
    if s.endswith("h"):
        return int(float(s[:-1]) * 3_600_000)
    if s.endswith("d"):
        return int(float(s[:-1]) * 86_400_000)
    return 900_000


def _finite(x: float) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return v == v and abs(v) != float("inf")


def validate_ohlcv(
    candles: list[OHLCV],
    *,
    timeframe: str = "15m",
    expect_utc_ms: bool = True,
) -> tuple[list[OHLCV], DataQualityReport]:
    """Validate and return cleaned chronological unique candles + report.

    Does NOT invent missing bars (no future/past fill of gaps).
    """
    report = DataQualityReport(rows=len(candles), timeframe=timeframe)
    if not candles:
        report.notes.append("empty")
        return [], report

    step = _tf_ms(timeframe)
    # sort + dedupe by timestamp (keep first)
    ordered = sorted(candles, key=lambda c: int(c.timestamp_ms))
    seen: set[int] = set()
    clean: list[OHLCV] = []
    prev_ts: int | None = None
    for c in ordered:
        ts = int(c.timestamp_ms)
        if ts in seen:
            report.duplicates += 1
            continue
        seen.add(ts)
        if expect_utc_ms and ts < 1_000_000_000_000:
            # likely seconds not ms
            report.invalid_rows += 1
            report.notes.append(f"non_ms_timestamp:{ts}")
            continue
        o, h, l, cl, v = float(c.open), float(c.high), float(c.low), float(c.close), float(c.volume)
        if not all(_finite(x) for x in (o, h, l, cl, v)):
            report.nan_or_inf += 1
            report.invalid_rows += 1
            continue
        if h < max(o, cl) or l > min(o, cl) or h < l or o <= 0 or cl <= 0:
            report.bad_ohlc += 1
            report.invalid_rows += 1
            continue
        if v < 0:
            report.invalid_rows += 1
            continue
        if v == 0:
            report.zero_volume += 1
        if prev_ts is not None and step > 0:
            gap = ts - prev_ts
            if gap > step * 1.5:
                # count missing intervals
                report.missing_intervals += max(0, int(round(gap / step)) - 1)
        prev_ts = ts
        clean.append(c)

    report.usable_rows = len(clean)
    if clean:
        report.start_time_ms = int(clean[0].timestamp_ms)
        report.end_time_ms = int(clean[-1].timestamp_ms)
    return clean, report
