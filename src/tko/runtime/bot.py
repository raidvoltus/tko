"""Main LIVE trading loop with Stage-4 lifecycle governor. LIVE only."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from tko.audit.audit_log import AuditLog
from tko.core.config import Settings
from tko.core.credentials import load_telegram, load_tokocrypto
from tko.core.types import OrderType, Side, Signal
from tko.exchange.tokocrypto import TokocryptoClient
from tko.execution.engine import ExecutionEngine
from tko.notify.telegram import TelegramNotifier
from tko.reconciliation.reconciler import Reconciler
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState
from tko.runtime.metrics import MetricsStore
from tko.runtime.watchdog import Heartbeat
from tko.strategy.btc import BtcAnalyzer
from tko.ml.filter import MlSignalFilter
from tko.ml.ohlcv_store import OhlcvStore

logger = logging.getLogger(__name__)
STABLE_LIKE = frozenset({"IDR", "USDT", "USDC", "BUSD", "USD", "BNB"})


class TradingBot:
    def __init__(self, settings: Settings, state_dir: Path) -> None:
        self.s = settings
        self.state_dir = state_dir
        self.lifecycle = LifecycleGovernor()
        creds = load_tokocrypto()
        self.client = TokocryptoClient(creds)
        self.audit = AuditLog(state_dir / "audit_log.jsonl")
        self.metrics = MetricsStore(state_dir / "metrics.json")
        self.heartbeat = Heartbeat(state_dir / "heartbeat.json")
        self.pnl = DailyPnLTracker(state_dir / "pnl_ledger.jsonl", timezone_name=settings.risk_timezone)
        self.risk = RiskEngine(settings, state_dir, pnl_tracker=self.pnl, audit=self.audit)
        self.positions_store = PositionStore(state_dir / "positions.json")
        self.strategy = BtcAnalyzer(settings)
        self.ml_filter = MlSignalFilter(
            enabled=bool(settings.ml_filter_enabled),
            model_path=Path(settings.ml_model_path) if settings.ml_model_path else None,
            min_confidence=float(settings.ml_min_confidence),
        )
        self.ohlcv_store = (
            OhlcvStore(state_dir / "ohlcv") if settings.ohlcv_store_enabled else None
        )
        self.execution = ExecutionEngine(
            self.client, settings, state_dir,
            risk=self.risk, positions_store=self.positions_store,
            audit=self.audit, metrics=self.metrics,
            lifecycle=self.lifecycle,
        )
        self.notify = TelegramNotifier(
            load_telegram() if settings.telegram_enabled else None, audit=self.audit,
        )
        self.reconciler = Reconciler(
            client=self.client, intents=self.execution.intents,
            positions=self.positions_store, audit=self.audit, min_dust=settings.min_base_dust,
        )
        self._running = False
        self._start_lock = threading.Lock()
        self._start_active = False
        self._worker_id = ""
        self._started_at = 0.0
        self._ipc = None
        self._last_reconcile = 0.0
        self._stop_requested = False
        self._recovery_attempts = 0

    def status_dict(self) -> dict:
        from tko.runtime.ipc_hooks import make_status_dict
        return make_status_dict(self)

    def _start_ipc(self) -> None:
        from tko.runtime.ipc_hooks import start_ipc
        start_ipc(self)

    def _stop_ipc(self) -> None:
        from tko.runtime.ipc_hooks import stop_ipc
        stop_ipc(self)

    def _hb(self, status: str | None = None) -> None:
        snap = self.lifecycle.snapshot()
        self.heartbeat.beat(
            status=status or snap.state.value,
            lifecycle=snap.state.value,
            trading_authorized=snap.trading_authorized,
            last_error=snap.last_error,
            process_alive=snap.process_alive,
            extra={
                "last_successful_recon_ts": snap.last_successful_recon_ts,
                "last_exchange_contact_ts": snap.last_exchange_contact_ts,
                "last_tick_ts": snap.last_tick_ts,
            },
        )

    def _recovery_backoff_sec(self) -> float:
        exp = min(max(self._recovery_attempts - 1, 0), 6)
        return float(min(60.0, max(1.0, (2 ** exp) * max(1.0, self.s.loop_interval_sec / 5.0))))

    def _halt(self, reason: str) -> None:
        self.lifecycle.force(LifecycleState.HALTED, reason=reason)
        self.metrics.set_status("HALTED", reason[:200])
        self._hb("HALTED")
        self.audit.record("ERROR", reason=f"runtime_halted:{reason[:200]}")
        try:
            self.notify.send(f"TKO HALTED: {reason[:300]}")
        except Exception:
            pass
        logger.critical("event=runtime_halted reason=%s", reason[:300])
