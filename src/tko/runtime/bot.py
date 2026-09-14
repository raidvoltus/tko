"""Main LIVE trading loop with Stage-4 lifecycle governor. LIVE only."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from tko.audit.audit_log import AuditLog
from tko.core.config import Settings
from tko.core.credentials import load_telegram, load_tokocrypto
from tko.core.types import Side, Signal
from tko.exchange.tokocrypto import TokocryptoClient
from tko.execution.engine import ExecutionEngine
from tko.marketdata.user_stream import UserDataStream, UserListenTokenClient
from tko.marketdata.ws_public import PublicMarketStream
from tko.ml.filter import MlSignalFilter
from tko.ml.ohlcv_store import OhlcvStore
from tko.notify.telegram import TelegramNotifier
from tko.reconciliation.reconciler import Reconciler
from tko.risk.engine import RiskEngine
from tko.risk.pnl_tracker import DailyPnLTracker
from tko.risk.position_store import PositionStore
from tko.runtime.lifecycle import LifecycleGovernor, LifecycleState
from tko.runtime.metrics import MetricsStore
from tko.runtime.readiness import evaluate_readiness
from tko.runtime.watchdog import Heartbeat
from tko.strategy.btc import BtcAnalyzer
from tko.strategy.ranker import best_tradeable, rank_candidates

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
        self._public_ws: PublicMarketStream | None = None
        self._user_stream: UserDataStream | None = None
        self._streams_started = False

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
        except Exception:  # noqa: BLE001,S110
            pass
        logger.critical("event=runtime_halted reason=%s", reason[:300])


    def _subscribe_universe_symbols(self) -> list[str]:
        """Symbols the bot actually trades (bases × quotes that resolve)."""
        symbols: list[str] = []
        bases = self.s.tradeable_base_list()
        quotes = self.s.quote_asset_list()
        for base in bases:
            for quote in quotes:
                try:
                    sym = self.client.resolve_symbol(base, quote)
                except Exception:  # noqa: BLE001
                    sym = None
                if sym and sym not in symbols:
                    symbols.append(sym)
        return symbols

    def _start_streams(self) -> bool:
        """Start public WS + user stream after recon. Fail-closed on hard error."""
        if self._streams_started:
            return True
        symbols = self._subscribe_universe_symbols()
        stale = float(getattr(self.s, "ws_stale_sec", 20.0) or 20.0)
        try:
            self._public_ws = PublicMarketStream(stale_sec=stale)
            if symbols:
                self._public_ws.subscribe_symbols(symbols, channel="ticker")
            self._public_ws.start()
            logger.info("event=ws_public_started symbols=%d", len(symbols))
        except Exception as exp:  # noqa: BLE001
            logger.error("event=ws_public_start_failed err=%s", exp)
            if bool(getattr(self.s, "require_ws_market", True)):
                return False
            self._public_ws = None

        try:
            creds = load_tokocrypto()
            token_client = UserListenTokenClient(
                api_key=creds.api_key.get_secret_value(),
                api_secret=creds.api_secret.get_secret_value(),
            )
            self._user_stream = UserDataStream(token_client)
            self._user_stream.start()
            logger.info("event=user_stream_started")
        except Exception as exp:  # noqa: BLE001
            logger.error("event=user_stream_start_failed err=%s", exp)
            if bool(getattr(self.s, "require_user_stream", True)):
                return False
            self._user_stream = None

        self._streams_started = True
        return True

    def _stop_streams(self) -> None:
        for label, stream in (("public_ws", self._public_ws), ("user_stream", self._user_stream)):
            if stream is None:
                continue
            try:
                stream.stop()
                logger.info("event=%s_stopped", label)
            except Exception as exp:  # noqa: BLE001
                logger.warning("event=%s_stop_error err=%s", label, exp)
        self._public_ws = None
        self._user_stream = None
        self._streams_started = False

    def _public_ws_fresh(self) -> bool:
        if self._public_ws is None:
            return not bool(getattr(self.s, "require_ws_market", True))
        max_age = float(getattr(self.s, "ws_stale_sec", 20.0) or 20.0)
        try:
            return bool(self._public_ws.health.is_fresh(max_age_sec=max_age))
        except Exception:  # noqa: BLE001
            return False

    def _user_stream_healthy(self) -> bool:
        if self._user_stream is None:
            return not bool(getattr(self.s, "require_user_stream", True))
        try:
            return bool(self._user_stream.health.is_healthy())
        except Exception:  # noqa: BLE001
            return False

    def _streams_readiness_ok(self) -> tuple[bool, tuple[str, ...]]:
        """Evaluate stream gates; returns (ok, reasons)."""
        report = evaluate_readiness(
            process_alive=True,
            exchange_ok=True,
            market_data_fresh=self._public_ws_fresh(),
            user_stream_healthy=self._user_stream_healthy(),
            recon_ok=True,
            risk_healthy=not self.risk.kill_switch_active(),
            kill_switch_off=not self.risk.kill_switch_active(),
            require_user_stream=bool(getattr(self.s, "require_user_stream", True)),
            require_ws_market=bool(getattr(self.s, "require_ws_market", True)),
        )
        return report.ready, report.reasons

    def _wait_streams_healthy(self, timeout_sec: float | None = None) -> bool:
        """Block until streams healthy or timeout. Returns False on failure."""
        import time as _time
        limit = float(timeout_sec if timeout_sec is not None else getattr(self.s, "ws_startup_timeout_sec", 45.0))
        deadline = _time.time() + max(1.0, limit)
        # First connection may need a few seconds; allow brief grace for "connected but no tick yet"
        grace_deadline = _time.time() + min(8.0, limit)
        while _time.time() < deadline:
            if self._stop_requested:
                return False
            ok, reasons = self._streams_readiness_ok()
            if ok:
                logger.info("event=streams_healthy reasons=none")
                return True
            # Soften: if only market_data_stale during grace and WS reports connected, keep waiting
            if _time.time() < grace_deadline and self._public_ws is not None:
                try:
                    if self._public_ws.health.connected and "user_stream_unhealthy" not in reasons:
                        _time.sleep(0.5)
                        continue
                except Exception as exp:  # noqa: BLE001
                    logger.debug("event=streams_grace_check_error err=%s", exp)
            logger.info("event=streams_wait reasons=%s", ",".join(reasons) or "unknown")
            _time.sleep(0.75)
        ok, reasons = self._streams_readiness_ok()
        if not ok:
            logger.error("event=streams_startup_timeout reasons=%s", ",".join(reasons))
        return ok

    def _enforce_stream_gates(self) -> bool:
        """If streams unhealthy while READY → DEGRADED + NO TRADE. Returns True if still tradeable."""
        if not self.lifecycle.trading_authorized:
            return False
        ok, reasons = self._streams_readiness_ok()
        if ok:
            return True
        reason = "stream_unhealthy:" + ",".join(reasons)
        logger.warning("event=stream_gate_fail reasons=%s → DEGRADED", ",".join(reasons))
        self.lifecycle.transition(LifecycleState.DEGRADED, reason=reason[:280])
        self.metrics.set_status("DEGRADED", reason[:200])
        self._hb("DEGRADED")
        try:
            self.audit.record("ERROR", reason=reason[:200])
        except Exception:  # noqa: BLE001,S110
            pass
        return False


    def _startup_barrier(self) -> bool:
        self.lifecycle.force(LifecycleState.STARTING, reason="startup")
        self._hb("STARTING")
        self.audit.record("DECISION", reason="runtime_starting")
        try:
            self.client.connect()
            self.lifecycle.mark_exchange_contact()
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001,S110
                pass
            return False
        daily_risk_ok = True

        if getattr(self.execution.intents, "corrupted", False):
            self._halt(f"intent_store_corrupted:{getattr(self.execution.intents, 'corruption_reason', '')[:200]}")
            return False
        if getattr(self.positions_store, "corrupted", False):
            self._halt(f"position_store_corrupted:{getattr(self.positions_store, 'corruption_reason', '')[:200]}")
            return False
        if getattr(self.risk, "baseline_corrupted", False):
            self._halt(f"risk_baseline_corrupted:{getattr(self.risk, 'baseline_corruption_reason', '')[:200]}")
            return False

        # P0: start live streams only after recon + risk gates; wait until healthy
        if not self._start_streams():
            self._halt("stream_start_failed")
            return False
        if not self._wait_streams_healthy():
            # Soft policy: if require flags are on, block READY
            ok, reasons = self._streams_readiness_ok()
            if not ok and (
                bool(getattr(self.s, "require_ws_market", True))
                or bool(getattr(self.s, "require_user_stream", True))
            ):
                self.lifecycle.transition(
                    LifecycleState.DEGRADED,
                    reason="streams_not_healthy:" + ",".join(reasons)[:200],
                )
                self.metrics.set_status("DEGRADED", "streams_not_healthy")
                self._hb("DEGRADED")
                logger.error("event=startup_streams_not_ready reasons=%s", ",".join(reasons))
                # Do not authorize READY — trading remains blocked
                return False

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
        except Exception:  # noqa: BLE001,S110
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
                    if self.lifecycle.state in (
                        LifecycleState.RECONCILING,
                        LifecycleState.STARTING,
                    ) and self._startup_barrier():
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
                    except Exception:  # noqa: BLE001,S110
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
                    if not self._enforce_stream_gates():
                        time.sleep(self.s.loop_interval_sec)
                        continue
                    self._tick()
                    self.lifecycle.mark_tick()
                    self.metrics.set_status("OK")
                except Exception as exc:
                    logger.exception("tick failed")
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
        self._stop_streams()
        try:
            self.client.close()
        except Exception as exp:  # noqa: BLE001
            logger.warning("client close: %s", exp)
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
            except Exception as exc:  # noqa: BLE001
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
        """Scan tradeable bases × quotes; rank by expected edge; TOP-1 still gated by RiskEngine."""
        if not self.lifecycle.trading_authorized:
            return
        quotes = self.s.quote_asset_list()
        bases = self.s.tradeable_base_list()
        candidates: list = []
        for base in bases:
            for quote in quotes:
                free_q = free_map.get(quote, 0.0)
                min_q = self.s.min_quote_balance_usdt if quote in ("USDT", "USDC", "USD") else self.s.min_quote_balance
                if free_q < min_q:
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
                except Exception as exp:  # noqa: BLE001
                    logger.warning("market data failed %s: %s", symbol, exp)
                    continue
                if last <= 0:
                    continue
                if self.ohlcv_store is not None:
                    try:
                        self.ohlcv_store.append(symbol, self.s.ohlcv_timeframe, ohlcv)
                    except Exception as exp:  # noqa: BLE001
                        logger.warning("ohlcv_store append failed %s: %s", symbol, exp)
                signal_decision = self.strategy.analyze(ohlcv)
                if self.ml_filter is not None and self.ml_filter.enabled:
                    try:
                        fd = self.ml_filter.filter(signal_decision, ohlcv)
                        if not getattr(fd, "allow", True) and signal_decision.signal == Signal.BUY:
                            from tko.strategy.btc import TradeDecision as _TD
                            signal_decision = _TD(Signal.HOLD, f"ml_block:{getattr(fd, 'reason', '')}", 0.0, last)
                    except Exception as exp:  # noqa: BLE001
                        logger.warning("ml filter failed: %s", exp)
                spread_pct = 0.0
                try:
                    bid = float(ticker.bid) if ticker.bid is not None else 0.0
                    ask = float(ticker.ask) if ticker.ask is not None else 0.0
                    if bid > 0 and ask > 0 and ask >= bid:
                        mid = (bid + ask) / 2.0
                        spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else 0.0
                except (TypeError, ValueError):
                    spread_pct = 0.0
                candidates.append((symbol, base, quote, signal_decision, spread_pct, free_q, last, ticker))

        if not candidates:
            return
        ranked_in = [(s, b, q, d, sp) for (s, b, q, d, sp, _fq, _last, _t) in candidates]
        ranked = rank_candidates(ranked_in)
        best = best_tradeable(ranked)
        if best is None:
            logger.info("event=no_trade reason=no_candidate_passed_edge_gate scanned=%d", len(candidates))
            return
        meta = next(c for c in candidates if c[0] == best.symbol and c[1] == best.base and c[2] == best.quote)
        _symbol, base, quote, _d, _sp, free_q, last, ticker = meta
        open_n = len([p for p in self.positions_store.all() if p.amount > 0])
        md_ts = None
        try:
            if getattr(ticker, "timestamp_ms", None):
                md_ts = float(ticker.timestamp_ms) / 1000.0
        except (TypeError, ValueError):
            md_ts = None
        decision = self.risk.evaluate_entry(
            symbol=best.symbol,
            quote_free=free_q,
            last_price=last,
            signal=best.decision,
            open_positions=open_n,
            quote_asset=quote,
            market_data_ts=md_ts,
        )
        if not decision.approved:
            logger.info("entry blocked %s: %s", best.symbol, decision.reason)
            return
        if not self.lifecycle.trading_authorized:
            return
        logger.info(
            "event=candidate_selected symbol=%s net_edge=%.4f strength=%.3f",
            best.symbol,
            best.edge.net_edge_pct,
            best.decision.strength,
        )
        result = self.execution.buy(best.symbol, base, quote, decision, last)
        if result:
            self.notify.send(
                f"BUY {best.symbol} filled={result.filled} avg={result.average} edge={best.edge.net_edge_pct:.3f}%"
            )

