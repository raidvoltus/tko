"""Lightweight file-based metrics (state/metrics.json)."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MetricsSnapshot:
    updated_at: float = 0.0
    status: str = "OK"
    orders_today: int = 0
    orders_success: int = 0
    orders_failed: int = 0
    submit_latency_ms_sum: float = 0.0
    submit_latency_count: int = 0
    daily_pnl: float = 0.0
    daily_notional: float = 0.0
    last_error: str = ""
    day: str = ""

    def avg_submit_latency_ms(self) -> float:
        if self.submit_latency_count <= 0:
            return 0.0
        return self.submit_latency_ms_sum / self.submit_latency_count


class MetricsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._m = MetricsSnapshot()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(self._m, k):
                    setattr(self._m, k, v)
        except Exception as exc:  # noqa: BLE001
            logger.warning("metrics load failed: %s", exc)

    def _save(self) -> None:
        self._m.updated_at = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self._m), indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def set_status(self, status: str, error: str = "") -> None:
        with self._lock:
            self._m.status = status
            if error:
                self._m.last_error = error[:300]
            self._save()

    def record_order(self, *, success: bool, latency_ms: float = 0.0) -> None:
        with self._lock:
            self._m.orders_today += 1
            if success:
                self._m.orders_success += 1
            else:
                self._m.orders_failed += 1
            if latency_ms > 0:
                self._m.submit_latency_ms_sum += latency_ms
                self._m.submit_latency_count += 1
            self._save()

    def update_pnl(self, day: str, pnl: float, notional: float) -> None:
        with self._lock:
            if self._m.day and self._m.day != day:
                self._m.orders_today = 0
                self._m.orders_success = 0
                self._m.orders_failed = 0
                self._m.submit_latency_ms_sum = 0.0
                self._m.submit_latency_count = 0
            self._m.day = day
            self._m.daily_pnl = pnl
            self._m.daily_notional = notional
            self._save()

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            return MetricsSnapshot(**asdict(self._m))
