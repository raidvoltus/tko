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

    def _startup_barrier(self) -> bool:
        self.lifecycle.force(LifecycleState.STARTING, reason="startup")
        self._hb("STARTING")
        self.audit.record("DECISION", reason="runtime_starting")
        try:
            self.client.connect()
            self.lifecycle.mark_exchange_contact()
        except Exception as exc:
            self._halt(f"exchange_connect_failed:{exc}")
            return False
        self.lifecycle.transition(LifecycleState.RECONCILING, reason="startup_recon")
        self._hb("RECONCILING")
        self.audit.record("DECISION", reason="runtime_reconciling")
        try:
            balances = self.client.fetch_balance()
            self.lifecycle.mark_exchange_contact()
            free_map = {a: b.free for a, b in balances.items()}

            def _on_confirmed(intent):
                side = Side.BUY if intent.side == "buy" else Side.SELL
                synthetic = self.execution._result_from_intent(intent, side)
                base = intent.symbol.split("/")[0] if "/" in intent.symbol else ""
                quote = intent.symbol.split("/")[1] if "/" in intent.symbol else ""
                from tko.execution.intent import OrderIntentStatus
                is_partial = intent.status == OrderIntentStatus.PARTIALLY_FILLED
                rem = float(synthetic.remaining or 0.0)
                self.execution._on_fill_confirmed(
                    intent, side, synthetic, base=base, quote=quote,
                    partial=is_partial, remaining=rem,
                )

            recon = self.reconciler.reconcile_all(free_map, on_confirmed=_on_confirmed)
            self.execution._hydrate_positions_memory()
            eq = sum(float(free_map.get(q, 0.0)) for q in self.s.quote_asset_list())
            self.risk.set_equity_baseline_if_empty(eq)
            if recon.notes:
                logger.info("reconcile notes=%s", recon.notes)
            if not recon.safe_to_trade:
                self._halt(
                    "position_discrepancy:"
                    + ";".join(d.note for d in recon.position_discrepancies)[:280]
                )
                return False
            self.lifecycle.mark_recon_ok()
        except Exception as exc:
            self._halt(f"startup_reconciliation_failed:{exc}")
            return False
        recon_ok = True
        positions_ok = True
        exchange_ok = True

        if self.risk.kill_switch_active():
            self.lifecycle.transition(LifecycleState.KILL, reason="kill_switch_active_on_startup")
            self._hb("KILL")
            self.metrics.set_status("KILL")
            self.audit.record("ERROR", reason="kill_switch_active_on_startup")
            return False
        kill_switch_clear = True

        if self.client.circuit_open:
            self._halt(f"circuit_open:{self.client.circuit_reason}")
            return False
        circuit_clear = True

        day = self.pnl.stats_for_day()
        self.metrics.update_pnl(day.day, day.realized_pnl, day.notional_traded)
        breach = self.risk.check_daily_limits_or_kill()
        if breach:
            self.lifecycle.transition(LifecycleState.KILL, reason=breach)
            self._hb("KILL")
            self.metrics.set_status("KILL", breach)
            self.audit.record("ERROR", reason=f"daily_risk_block:{breach[:200]}")
            try:
                self.notify.send(f"TKO KILL (pre-ready risk): {breach[:300]}")
            except Exception:
                pass
            return False
        daily_risk_ok = True

        if not self.lifecycle.authorize_ready(
            reason="startup_ok",
            recon_ok=recon_ok,
            kill_switch_clear=kill_switch_clear,
            circuit_clear=circuit_clear,
            daily_risk_ok=daily_risk_ok,
            positions_ok=positions_ok,
            exchange_ok=exchange_ok,
        ):
            self._halt("cannot_enter_ready:validation_gates_failed")
            return False
        self.metrics.set_status("OK")
        self._hb("READY")
        self.audit.record("DECISION", reason="runtime_ready", extra={"day": day.day})
        try:
            self.notify.send(f"TKO READY LIVE day={day.day} pnl={day.realized_pnl:.4f}")
        except Exception:
            pass
        logger.info("event=runtime_ready loop_interval=%.0fs", self.s.loop_interval_sec)
        return True

    def start(self) -> None:
        with self._start_lock:
            if self._start_active:
                logger.warning("event=start_rejected reason=already_running")
                return
            self._start_active = True
        self._running = True
        self._stop_requested = False
        self._recovery_attempts = 0
        self.lifecycle.mark_process_alive()
        self._start_ipc()
        if not self._startup_barrier():
            logger.error("Startup barrier failed - autonomous recovery may retry")
            while self._running and not self._stop_requested:
                self._hb()
                if self.lifecycle.state in (LifecycleState.STOPPING, LifecycleState.STOPPED):
                    self._clear_start_active()
                    return
                if self.lifecycle.state in (LifecycleState.HALTED, LifecycleState.KILL, LifecycleState.RECOVERY):
                    self._recovery_attempts += 1
                    backoff = self._recovery_backoff_sec()
                    logger.warning(
                        "event=autonomous_recovery phase=startup attempt=%s backoff=%.1fs state=%s",
                        self._recovery_attempts,
                        backoff,
                        self.lifecycle.state.value,
                    )
                    time.sleep(backoff)
                    if self.lifecycle.state == LifecycleState.KILL and self.risk.kill_switch_active():
                        logger.warning("event=kill_recovery_blocked reason=kill_switch_still_active")
                        continue
                    if self.lifecycle.state == LifecycleState.RECOVERY:
                        self.lifecycle.complete_recovery_to_reconciling(reason="startup_recovery_progress")
                    elif self.lifecycle.begin_recovery(reason=f"startup_recovery_{self._recovery_attempts}"):
                        self.lifecycle.complete_recovery_to_reconciling(reason="startup_recovery_recon")
                    if self.lifecycle.state in (LifecycleState.RECONCILING, LifecycleState.STARTING):
                        if self._startup_barrier():
                            self._recovery_attempts = 0
                            break
                    continue
                time.sleep(self.s.loop_interval_sec)
            else:
                self._clear_start_active()
                return
        try:
            while self._running and not self._stop_requested:
                if not self.lifecycle.trading_authorized:
                    self._hb()
                    if self.lifecycle.state == LifecycleState.KILL:
                        self._recovery_attempts += 1
                        backoff = self._recovery_backoff_sec()
                        time.sleep(backoff)
                        if self.risk.kill_switch_active():
                            continue
                        if self.lifecycle.begin_recovery(reason=f"kill_recovery_{self._recovery_attempts}"):
                            self.lifecycle.complete_recovery_to_reconciling(reason="kill_recovery_recon")
                            if self._startup_barrier():
                                self._recovery_attempts = 0
                        continue
                    if self.lifecycle.state in (LifecycleState.STOPPING, LifecycleState.STOPPED):
                        break
                    if self.lifecycle.state == LifecycleState.HALTED:
                        self._recovery_attempts += 1
                        time.sleep(self._recovery_backoff_sec())
                        if self.lifecycle.begin_recovery(reason=f"auto_recovery_{self._recovery_attempts}"):
                            self.lifecycle.complete_recovery_to_reconciling(reason="auto_recovery_recon")
                            if self._startup_barrier():
                                self._recovery_attempts = 0
                        continue
                    if self.lifecycle.state == LifecycleState.RECOVERY:
                        self.lifecycle.complete_recovery_to_reconciling(reason="recovery_progress")
                        time.sleep(self.s.loop_interval_sec)
                        continue
                    time.sleep(self.s.loop_interval_sec)
                    continue
                self._hb("READY")
                if self.s.telegram_kill_command:
                    try:
                        self.notify.poll_kill_command(self.state_dir / "KILL")
                    except Exception:
                        pass
                if self.risk.kill_switch_active():
                    self.lifecycle.transition(LifecycleState.KILL, reason="kill_switch")
                    self.metrics.set_status("KILL")
                    self._hb("KILL")
                    self.notify.send("TKO: kill switch ACTIVE - not trading")
                    time.sleep(self.s.loop_interval_sec)
                    continue
                if self.client.circuit_open:
                    self.lifecycle.transition(LifecycleState.DEGRADED, reason=f"circuit:{self.client.circuit_reason}")
                    self.metrics.set_status("ERROR", "circuit_open")
                    self._hb("DEGRADED")
                    time.sleep(self.s.loop_interval_sec)
                    continue
                try:
                    breach = self.risk.check_daily_limits_or_kill()
                    if breach:
                        self.lifecycle.transition(LifecycleState.KILL, reason=breach)
                        self.metrics.set_status("KILL", breach)
                        self._hb("KILL")
                        time.sleep(self.s.loop_interval_sec)
                        continue
                    self._tick()
                    self.lifecycle.mark_tick()
                    self.metrics.set_status("OK")
                except Exception as exc:
                    logger.exception("tick failed: %s", exc)
                    self.audit.record("ERROR", reason=f"tick:{exc}")
                    self.metrics.set_status("ERROR", str(exc)[:200])
                    self.lifecycle.transition(LifecycleState.DEGRADED, reason=f"tick:{exc}")
                    self._hb("DEGRADED")
                time.sleep(self.s.loop_interval_sec)
        finally:
            if self.lifecycle.state not in (LifecycleState.STOPPED, LifecycleState.STOPPING):
                self.stop()

    def _clear_start_active(self) -> None:
        with self._start_lock:
            self._start_active = False

    def request_shutdown(self) -> None:
        self._stop_requested = True
        self._running = False
        self.lifecycle.request_stop()

    def stop(self) -> None:
        self._stop_requested = True
        self._running = False
        if self.lifecycle.state == LifecycleState.STOPPED:
            self._clear_start_active()
            return
        self.lifecycle.request_stop()
        if self.lifecycle.state != LifecycleState.STOPPING:
            self.lifecycle.force(LifecycleState.STOPPING, reason="shutdown_requested")
        self.lifecycle.mark_process_stopped()
        self._hb("STOPPING")
        self.audit.record("DECISION", reason="runtime_stopping")
        try:
            self.client.close()
        except Exception as exc:
            logger.warning("client close: %s", exc)
        self.lifecycle.force(LifecycleState.STOPPED, reason="shutdown_complete")
        self._hb("STOPPED")
        self.metrics.set_status("STOPPED")
        self.audit.record("DECISION", reason="runtime_stopped")
        self._stop_ipc()
        self._clear_start_active()
        logger.info("event=runtime_stopped")

    def _tick(self) -> None:
        if not self.lifecycle.trading_authorized:
            return
        balances = self.client.fetch_balance()
        self.lifecycle.mark_exchange_contact()
        free_map = {a: b.free for a, b in balances.items() if b.free > 0}
        if self._manage_positions(free_map):
            return
        self._try_buy_primary(free_map)

    def _manage_positions(self, free_map: dict[str, float]) -> bool:
        if not self.lifecycle.trading_authorized:
            return False
        bases = self.s.tradeable_base_list()
        quotes = self.s.quote_asset_list()
        acted = False
        for base in bases:
            free_base = free_map.get(base, 0.0)
            if free_base <= self.s.min_base_dust:
                continue
            if base in STABLE_LIKE and base != self.s.base_asset.upper():
                continue
            symbol = None
            for q in quotes:
                symbol = self.client.resolve_symbol(base, q)
                if symbol:
                    break
            if not symbol:
                continue
            try:
                ticker = self.client.fetch_ticker(symbol)
                last = float(ticker.last or 0)
            except Exception as exc:
                logger.warning("ticker failed %s: %s", symbol, exp)
                continue
            if last <= 0:
                continue
            entry = self.execution.load_entry_price(symbol) or last
            decision = self.risk.evaluate_exit(
                symbol=symbol, base_free=free_base, last_price=last, entry_price=entry
            )
            if not self.lifecycle.trading_authorized:
                return True
            if decision.approved and decision.size and decision.size > 0:
                self.execution.submit_sell(symbol, decision.size, last, reason=decision.reason)
                acted = True
        return acted

    def _try_buy_primary(self, free_map: dict[str, float]) -> None:
        if not self.lifecycle.trading_authorized:
            return
        base = self.s.base_asset.upper()
        for q in self.s.quote_asset_list():
            symbol = self.client.resolve_symbol(base, q)
            if not symbol:
                continue
            quote_free = float(free_map.get(q, 0.0))
            try:
                ticker = self.client.fetch_ticker(symbol)
                last = float(ticker.last or 0)
            except Exception:
                continue
            if last <= 0:
                continue
            decision = self.risk.evaluate_entry(
                symbol=symbol,
                quote_free=quote_free,
                last_price=last,
                signal=None,
            )
            if not self.lifecycle.trading_authorized:
                return
            if decision.approved and decision.size and decision.size > 0:
                self.execution.submit_buy(symbol, decision.size, last, reason=decision.reason)
                return
