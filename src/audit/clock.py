"""Clock discipline: monotonic elapsed control + UTC wall timestamps + drift detection."""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ClockSnapshot:
    utc_unix: float
    mono: float
    drift_ms: float
    healthy: bool


class ClockDiscipline:
    """
    - mono: time.monotonic() for elapsed intervals (not subject to wall clock jumps)
    - utc_unix: time.time() for event ordering / exchange alignment
    - drift_ms: external offset (e.g. exchange server time - local)
    """

    def __init__(self, max_drift_ms: float = 3000.0):
        self.max_drift_ms = float(max_drift_ms)
        self._offset_ms = 0.0
        self._mono0 = time.monotonic()
        self._utc0 = time.time()

    def set_exchange_offset_ms(self, offset_ms: float) -> None:
        if offset_ms is None:
            return
        try:
            v = float(offset_ms)
        except (TypeError, ValueError):
            return
        if v != v or abs(v) == float("inf"):  # NaN/Inf
            return
        self._offset_ms = v

    def snapshot(self) -> ClockSnapshot:
        mono = time.monotonic()
        utc = time.time()
        drift = abs(self._offset_ms)
        return ClockSnapshot(
            utc_unix=utc,
            mono=mono,
            drift_ms=self._offset_ms,
            healthy=drift <= self.max_drift_ms,
        )

    def elapsed_mono(self, since_mono: float) -> float:
        return max(0.0, time.monotonic() - since_mono)

    def now_utc(self) -> float:
        return time.time()

    def now_mono(self) -> float:
        return time.monotonic()
