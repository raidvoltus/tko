"""Main LIVE trading loop with Stage-4 lifecycle governor. LIVE only."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from tko.audit.audit_log import AuditLog
from tko.core.config import Settings
from tko.core.credentials import load_telegram, load_tokocrypto
from tko.core.types import Signal
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
        # Stage 5: ML is additive filter only (default OFF). Never bypasses risk/lifecycle.
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
        self._last_reconcile = 0.0
        self._stop_requested = False
        self._recovery_attempts = 0  # monotonically increases for backoff only (INV-54)

    def _hb(self, status: str | None = None) -> None:
        snap = self.lifecycle.snapshot()
        self.heartbeat.beat(
            status=status or snap.state.value,
            lifecycle=snap.state.value,
            trading_authorized=snap.trading_authorized,
            last_error=snap.last_error,
            extra={
                "last_successful_recon_ts": snap.last_successful_recon_ts,
                "last_exchange_contact_ts": snap.last_exchange_contact_ts,
                "last_tick_ts": snap.last_tick_ts,
            },
        )

    def _recovery_backoff_sec(self) -> float:
        """Bounded exponential backoff for autonomous recovery (INV-54). Never stops retrying."""
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
            notes = self.positions_store.reconcile_with_balances(
                free_map, min_dust=self.s.min_base_dust, stable_like=STABLE_LIKE
            )
            self.execution._hydrate_positions_memory()
            eq = sum(float(free_map.get(q, 0.0)) for q in self.s.quote_asset_list())
            self.risk.set_equity_baseline_if_empty(eq)
            self.reconciler.reconcile_all(free_map)
            self.lifecycle.mark_recon_ok()
            if notes:
                logger.info("position reconcile notes=%s", notes)
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
        self._running = True
        self._stop_requested = False
        self._recovery_attempts = 0
        if not self._startup_barrier():
            logger.error("Startup barrier failed - autonomous recovery may retry")
            while self._running and not self._stop_requested:
                self._hb()
                if self.lifecycle.state in (LifecycleState.STOPPING, LifecycleState.STOPPED):
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
                return
        try:
            while self._running and not self._stop_requested:
                if not self.lifecycle.trading_authorized:
                    self._hb()
                    if self.lifecycle.state == LifecycleState.KILL:
                        self._recovery_attempts += 1
                        backoff = self._recovery_backoff_sec()
                        logger.warning(
                            "event=kill_autonomous_recovery attempt=%s backoff=%.1fs",
                            self._recovery_attempts,
                            backoff,
                        )
                        time.sleep(backoff)
                        if self.risk.kill_switch_active():
                            logger.warning("event=kill_recovery_blocked reason=kill_switch_still_active")
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
                        backoff = self._recovery_backoff_sec()
                        logger.warning(
                            "event=autonomous_recovery attempt=%s backoff=%.1fs",
                            self._recovery_attempts,
                            backoff,
                        )
                        time.sleep(backoff)
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
                    self.notify.send("TKO: exchange circuit breaker OPEN")
                    time.sleep(self.s.loop_interval_sec)
                    continue
                try:
                    breach = self.risk.check_daily_limits_or_kill()
                    if breach:
                        self.lifecycle.transition(LifecycleState.KILL, reason=breach)
                        self.metrics.set_status("KILL", breach)
                        self._hb("KILL")
                        self.notify.send(f"TKO kill: {breach}")
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

    def request_shutdown(self) -> None:
        self.lifecycle.request_stop()
        self._stop_requested = True
        self._running = False

    def stop(self) -> None:
        if self.lifecycle.state == LifecycleState.STOPPED:
            return
        self._stop_requested = True
        self._running = False
        self.lifecycle.request_stop()
        if self.lifecycle.state != LifecycleState.STOPPING:
            self.lifecycle.force(LifecycleState.STOPPING, reason="shutdown_requested")
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
        logger.info("event=runtime_stopped")

    def _tick(self) -> None:
        if not self.lifecycle.trading_authorized:
            return
        balances = self.client.fetch_balance()
        self.lifecycle.mark_exchange_contact()
        free_map = {a: b.free for a, b in balances.items() if b.free > 0}
        logger.info("balances free=%s", {k: round(v, 8) for k, v in sorted(free_map.items())})
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
            quote_used = None
            for q in quotes:
                symbol = self.client.resolve_symbol(base, q)
                if symbol:
                    quote_used = q
                    break
            if not symbol or not quote_used:
                continue
            try:
                ticker = self.client.fetch_ticker(symbol)
                last = float(ticker.last or 0)
            except Exception as exc:
                logger.warning("ticker failed %s: %s", symbol, exc)
                continue
            if last <= 0:
                continue
            entry = self.execution.load_entry_price(symbol) or last
            decision = self.risk.evaluate_exit(
                symbol=symbol, base_free=free_base, last_price=last, entry_price=entry
            )
            if decision.approved and decision.size_base > 0:
                if not self.lifecycle.trading_authorized:
                    return acted
                result = self.execution.sell(symbol, decision, last, base=base, quote=quote_used)
                if result:
                    acted = True
                    self.notify.send(f"SELL {symbol} filled={result.filled} avg={result.average}")
        return acted

    def _try_buy_primary(self, free_map: dict[str, float]) -> None:
        if not self.lifecycle.trading_authorized:
            return
        base = self.s.base_asset.upper()
        quotes = self.s.quote_asset_list()
        for quote in quotes:
            free_q = free_map.get(quote, 0.0)
            if free_q < self.s.min_quote_balance:
                continue
            symbol = self.client.resolve_symbol(base, quote)
            if not symbol:
                continue
            try:
                ohlcv = self.client.fetch_ohlcv(
                    symbol,
                    timeframe=self.s.ohlcv_timeframe,
                    limit=self.s.ohlcv_limit,
                )
                ticker = self.client.fetch_ticker(symbol)
                last = float(ticker.last or 0)
            except Exception as exc:
                logger.warning("market data failed %s: %s", symbol, exc)
                continue
            if last <= 0:
                continue
            if self.ohlcv_store is not None:
                try:
                    self.ohlcv_store.append(symbol, self.s.ohlcv_timeframe, ohlcv)
                except Exception as exc:
                    logger.warning("ohlcv store append failed: %s", exc)
            # Rule-based primary decision (BtcAnalyzer)
            decision_td = self.strategy.analyze(ohlcv)
            if decision_td.signal != Signal.BUY:
                continue
            # Optional ML filter — fail-closed for BUY when enabled without model
            filt = self.ml_filter.filter(
                decision_td,
                ohlcv,
                rsi_period=self.s.rsi_period,
                ema_fast=self.s.ema_fast,
                ema_slow=self.s.ema_slow,
            )
            if not filt.allow:
                logger.info("event=ml_filter_block reason=%s", filt.reason)
                continue
            decision = self.risk.evaluate_entry(
                symbol=symbol,
                quote_free=free_q,
                last_price=last,
                signal=decision_td.signal,
            )
            if not decision.approved:
                continue
            if not self.lifecycle.trading_authorized:
                return
            result = self.execution.buy(symbol, base, quote, decision, last)
            if result:
                self.notify.send(f"BUY {symbol} filled={result.filled} avg={result.average}")
            return
