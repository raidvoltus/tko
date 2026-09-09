"""Risk limits, durable PnL, hard caps, kill switch, and atomic notional reservation."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from tko.core.config import Settings
from tko.core.types import Signal
from tko.risk.pnl_tracker import DailyPnLTracker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    size_quote: float
    size_base: float


class RiskEngine:
    """Hard risk caps with process-wide atomic daily-notional reservation (S5-B1 / INV-60).

    Reservation protocol
    --------------------
    risk approval → try_reserve_notional(id, amount) → create intent → LIVE POST
                 → on CONFIRMED: commit_reservation
                 → on REJECTED / definitive failure: release_reservation
                 → on UNKNOWN / GOVERNOR_AUTONOMOUS: keep reservation (order may exist)

    Concurrent callers share one lock; used + reserved can never exceed max_daily_notional.
    """

    def __init__(
        self,
        settings: Settings,
        state_dir: Path,
        pnl_tracker: DailyPnLTracker | None = None,
        audit: object | None = None,
    ) -> None:
        self.s = settings
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.pnl = pnl_tracker or DailyPnLTracker(
            state_dir / "pnl_ledger.jsonl",
            timezone_name=settings.risk_timezone,
        )
        self.audit = audit
        self._equity_baseline = float(settings.daily_equity_baseline or 0.0)
        # S5-B1: atomic daily-notional budget
        self._budget_lock = threading.RLock()
        self._reserved: dict[str, float] = {}  # reservation_id → amount

    # ------------------------------------------------------------------ budget
    def reserved_notional(self) -> float:
        with self._budget_lock:
            return float(sum(self._reserved.values()))

    def effective_daily_used(self) -> float:
        """Confirmed notional + outstanding reservations."""
        with self._budget_lock:
            return float(self.pnl.today_notional()) + float(sum(self._reserved.values()))

    def try_reserve_notional(self, amount: float, *, reservation_id: str) -> tuple[bool, str]:
        """Atomically reserve *amount* against max_daily_notional.

        Idempotent for the same reservation_id (re-reserve replaces prior amount).
        Returns (ok, reason). Fail-closed when limit would be breached.
        """
        if amount <= 0:
            return False, "reserve amount must be > 0"
        rid = (reservation_id or "").strip()
        if not rid:
            return False, "reservation_id required"
        limit = float(self.s.max_daily_notional or 0.0)
        with self._budget_lock:
            # drop prior reservation for same id so re-entry is safe
            prior = float(self._reserved.pop(rid, 0.0))
            used = float(self.pnl.today_notional())
            outstanding = float(sum(self._reserved.values()))
            projected = used + outstanding + float(amount)
            if limit > 0 and projected > limit + 1e-9:
                # restore prior if any
                if prior > 0:
                    self._reserved[rid] = prior
                reason = (
                    f"daily notional reserve blocked: used={used:.4f} "
                    f"reserved={outstanding:.4f} request={amount:.4f} "
                    f"limit={limit:.4f}"
                )
                logger.warning("event=notional_reserve_denied id=%s %s", rid, reason)
                return False, reason
            self._reserved[rid] = float(amount)
            logger.info(
                "event=notional_reserved id=%s amount=%.4f used=%.4f outstanding=%.4f limit=%.4f",
                rid,
                amount,
                used,
                outstanding + amount,
                limit,
            )
            return True, "reserved"

    def release_reservation(self, reservation_id: str) -> None:
        """Drop a reservation without recording a fill (reject / cancelled path)."""
        rid = (reservation_id or "").strip()
        if not rid:
            return
        with self._budget_lock:
            prev = self._reserved.pop(rid, None)
        if prev is not None:
            logger.info("event=notional_released id=%s amount=%.4f", rid, prev)

    def commit_reservation(
        self,
        reservation_id: str,
        *,
        side: str,
        symbol: str,
        actual_notional: float,
        pnl: float = 0.0,
        order_id: str = "",
        client_order_id: str = "",
    ) -> None:
        """Release reservation and durable-record the fill under the same lock window."""
        rid = (reservation_id or "").strip()
        with self._budget_lock:
            if rid:
                self._reserved.pop(rid, None)
            self.pnl.record_trade(
                side=side,
                symbol=symbol,
                notional=abs(float(actual_notional)),
                pnl=float(pnl),
                order_id=order_id,
                client_order_id=client_order_id or rid,
            )
        logger.info(
            "event=notional_committed id=%s actual=%.4f",
            rid,
            actual_notional,
        )
        self.check_daily_limits_or_kill()

    def commit_partial_and_rereserve(
        self,
        reservation_id: str,
        *,
        side: str,
        symbol: str,
        filled_notional: float,
        remaining_reserve: float,
        pnl: float = 0.0,
        order_id: str = "",
        client_order_id: str = "",
    ) -> tuple[bool, str]:
        """Account filled portion and keep a residual reservation for open remainder (S5-W2)."""
        rid = (reservation_id or "").strip()
        with self._budget_lock:
            if rid:
                self._reserved.pop(rid, None)
            if filled_notional and abs(float(filled_notional)) > 0:
                self.pnl.record_trade(
                    side=side,
                    symbol=symbol,
                    notional=abs(float(filled_notional)),
                    pnl=float(pnl),
                    order_id=order_id,
                    client_order_id=client_order_id or rid,
                )
            rem = float(remaining_reserve or 0.0)
            if rid and rem > 0:
                used = float(self.pnl.today_notional())
                outstanding = float(sum(self._reserved.values()))
                limit = float(self.s.max_daily_notional or 0.0)
                # S5-B4: fail-closed — never let residual push used+reserved over limit
                if limit > 0:
                    headroom = max(0.0, limit - used - outstanding)
                    if rem > headroom + 1e-9:
                        logger.critical(
                            "event=partial_rereserve_clamped id=%s requested=%.4f "
                            "headroom=%.4f used=%.4f limit=%.4f",
                            rid, rem, headroom, used, limit,
                        )
                        rem = headroom
                if rem > 0:
                    self._reserved[rid] = rem
        logger.info(
            "event=notional_partial_commit id=%s filled=%.4f residual_reserve=%.4f",
            rid, filled_notional, remaining_reserve,
        )
        self.check_daily_limits_or_kill()
        return True, "partial_committed"

    def rehydrate_reservations_from_intents(self, intents: list) -> int:
        """Rebuild in-memory reservations from durable BUY intents after restart (S5-Recovery).

        Intent.quote_amount is the SSOT for outstanding reserved budget while status is
        still blocking (SUBMITTING/UNKNOWN/RECON/PARTIAL/GOVERNOR/…).
        Already-committed fills live in the PnL ledger and must not be double-counted.
        """
        restored = 0
        with self._budget_lock:
            self._reserved.clear()
            for intent in intents:
                side = str(getattr(intent, "side", "") or "").lower()
                if side != "buy":
                    continue
                cid = str(getattr(intent, "client_order_id", "") or "").strip()
                if not cid:
                    continue
                # Prefer residual: quote_amount - filled*avg when partial
                quote_amt = float(getattr(intent, "quote_amount", 0) or 0)
                filled = float(getattr(intent, "filled", 0) or 0)
                avg = getattr(intent, "average", None)
                avg_f = float(avg) if avg is not None else 0.0
                filled_notional = filled * avg_f if filled > 0 and avg_f > 0 else 0.0
                residual = max(0.0, quote_amt - filled_notional)
                if residual <= 0:
                    continue
                status = getattr(intent, "status", None)
                status_val = getattr(status, "value", str(status or ""))
                terminal = {"CONFIRMED", "REJECTED", "FAILED"}
                if status_val in terminal:
                    continue
                self._reserved[cid] = residual
                restored += 1
                logger.info(
                    "event=reservation_rehydrated id=%s residual=%.4f status=%s",
                    cid, residual, status_val,
                )
        logger.info("event=reservation_rehydrate_done count=%d total=%.4f", restored, self.reserved_notional())
        return restored

    # ----------------------------------------------------------- kill / equity
    def _risk_block(self, reason: str) -> None:
        if self.audit is not None:
            try:
                self.audit.record("RISK_BLOCK", reason=reason)  # type: ignore[attr-defined]
            except Exception:
                pass

    def set_equity_baseline_if_empty(self, equity: float) -> None:
        if self._equity_baseline <= 0 and equity > 0:
            self._equity_baseline = float(equity)
            logger.info("event=equity_baseline_set value=%.8f", self._equity_baseline)

    def kill_switch_active(self) -> bool:
        return (self.state_dir / "KILL").exists() or Path(self.s.kill_switch_file).exists()

    def activate_kill_switch(self, reason: str = "stop") -> None:
        p = self.state_dir / "KILL"
        p.write_text(reason[:500], encoding="utf-8")
        logger.warning("KILL SWITCH ACTIVATED reason=%s", reason)
        if self.audit is not None:
            try:
                self.audit.record("KILL_SWITCH", reason=reason[:300])  # type: ignore[attr-defined]
            except Exception:
                pass

    def clear_kill_switch(self) -> None:
        for p in (self.state_dir / "KILL", Path(self.s.kill_switch_file)):
            if p.exists():
                p.unlink(missing_ok=True)

    def daily_loss_breached(self) -> bool:
        if self.s.max_daily_loss_pct <= 0 or self._equity_baseline <= 0:
            return False
        pnl = self.pnl.today_realized_pnl()
        loss_pct = (-pnl / self._equity_baseline) * 100.0 if pnl < 0 else 0.0
        return loss_pct >= abs(self.s.max_daily_loss_pct)

    def check_daily_limits_or_kill(self) -> str | None:
        if self.daily_loss_breached():
            pnl = self.pnl.today_realized_pnl()
            reason = (
                f"daily loss limit breached: pnl={pnl:.4f} "
                f"limit={self.s.max_daily_loss_pct}% of baseline={self._equity_baseline:.4f}"
            )
            self.activate_kill_switch(reason)
            return reason
        return None

    # ----------------------------------------------------------- sizing helpers
    def _size_buy(
        self,
        free_quote: float,
        last_price: float,
        open_positions: int,
        *,
        quote_asset: str | None = None,
    ) -> RiskDecision:
        if self.kill_switch_active():
            self._risk_block("kill switch active")
            return RiskDecision(False, "kill switch active", 0.0, 0.0)
        breach = self.check_daily_limits_or_kill()
        if breach:
            return RiskDecision(False, breach, 0.0, 0.0)
        min_q = self.s.min_balance_for_quote(quote_asset or self.s.quote_asset)
        if free_quote < min_q:
            return RiskDecision(
                False, f"quote balance {free_quote:.8f} < min {min_q:.8f}", 0.0, 0.0
            )
        if open_positions >= self.s.max_open_positions:
            return RiskDecision(False, "max open positions reached", 0.0, 0.0)
        if last_price <= 0:
            return RiskDecision(False, "invalid price", 0.0, 0.0)
        size_quote = free_quote * (self.s.max_position_pct / 100.0)
        size_quote = min(size_quote, free_quote * 0.95)
        if self.s.max_order_notional > 0:
            size_quote = min(size_quote, float(self.s.max_order_notional))
        # Use effective (confirmed + reserved) so concurrent approvals cannot overshoot
        if self.s.max_daily_notional > 0:
            used_eff = self.effective_daily_used()
            remaining = float(self.s.max_daily_notional) - used_eff
            if remaining <= 0:
                return RiskDecision(
                    False,
                    f"max daily notional reached ({used_eff:.4f}/{self.s.max_daily_notional:.4f})",
                    0.0,
                    0.0,
                )
            size_quote = min(size_quote, remaining)
        if size_quote < min_q * 0.5:
            return RiskDecision(False, "computed size too small", 0.0, 0.0)
        return RiskDecision(True, "approved", size_quote, size_quote / last_price)

    def evaluate_buy(
        self,
        free_quote: float,
        last_price: float,
        open_positions: int,
        *,
        quote_asset: str | None = None,
    ) -> RiskDecision:
        return self._size_buy(
            free_quote, last_price, open_positions, quote_asset=quote_asset
        )

    def evaluate_entry(
        self,
        *,
        symbol: str,
        quote_free: float,
        last_price: float,
        signal: object = None,
        open_positions: int = 0,
        quote_asset: str | None = None,
    ) -> RiskDecision:
        """Bot-facing entry gate (alias of evaluate_buy with named kwargs)."""
        if signal is not None:
            sig_ok = False
            try:
                if signal == Signal.BUY:
                    sig_ok = True
            except Exception:
                pass
            if not sig_ok:
                val = getattr(signal, "value", None)
                if val is not None and str(val).upper() == "BUY":
                    sig_ok = True
                elif str(signal).upper() in ("BUY", "SIGNAL.BUY"):
                    sig_ok = True
            if not sig_ok:
                return RiskDecision(False, f"signal not BUY: {signal}", 0.0, 0.0)
        qa = quote_asset
        if qa is None and symbol and "/" in symbol:
            qa = symbol.split("/", 1)[1]
        return self._size_buy(quote_free, last_price, open_positions, quote_asset=qa)

    def evaluate_sell(
        self,
        free_base: float,
        entry_price: float | None,
        last_price: float,
        signal_sell: bool,
        *,
        estimated_notional: float | None = None,
    ) -> RiskDecision:
        if self.kill_switch_active():
            if free_base > 0:
                return RiskDecision(True, "kill switch — force sell", 0.0, free_base)
            return RiskDecision(False, "kill switch active", 0.0, 0.0)
        if free_base <= 0:
            return RiskDecision(False, "no base balance", 0.0, 0.0)
        if last_price <= 0:
            return RiskDecision(False, "invalid price", 0.0, 0.0)
        notional = (
            estimated_notional
            if estimated_notional is not None
            else free_base * last_price
        )
        if self.s.max_order_notional > 0 and notional > self.s.max_order_notional:
            free_base = min(free_base, float(self.s.max_order_notional) / last_price)
        if entry_price and entry_price > 0:
            pnl_pct = (last_price - entry_price) / entry_price * 100.0
            if pnl_pct >= self.s.take_profit_pct:
                return RiskDecision(True, f"take profit {pnl_pct:.2f}%", 0.0, free_base)
            if pnl_pct <= -abs(self.s.stop_loss_pct):
                return RiskDecision(True, f"stop loss {pnl_pct:.2f}%", 0.0, free_base)
        if signal_sell:
            return RiskDecision(True, "strategy SELL signal", 0.0, free_base)
        return RiskDecision(False, "hold position", 0.0, 0.0)

    def evaluate_exit(
        self,
        *,
        symbol: str,
        base_free: float,
        last_price: float,
        entry_price: float | None = None,
        signal_sell: bool = False,
    ) -> RiskDecision:
        """Bot-facing exit gate (alias of evaluate_sell with named kwargs)."""
        return self.evaluate_sell(
            free_base=base_free,
            entry_price=entry_price,
            last_price=last_price,
            signal_sell=signal_sell,
            estimated_notional=(base_free * last_price) if last_price > 0 else None,
        )

    def record_fill(
        self,
        *,
        side: str,
        symbol: str,
        notional: float,
        pnl: float = 0.0,
        order_id: str = "",
        client_order_id: str = "",
    ) -> None:
        """Legacy fill recorder. Prefer commit_reservation when a reservation exists."""
        rid = (client_order_id or "").strip()
        with self._budget_lock:
            if rid and rid in self._reserved:
                self._reserved.pop(rid, None)
            self.pnl.record_trade(
                side=side,
                symbol=symbol,
                notional=notional,
                pnl=pnl,
                order_id=order_id,
                client_order_id=client_order_id,
            )
        self.check_daily_limits_or_kill()
