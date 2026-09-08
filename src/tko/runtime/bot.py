"""Main LIVE trading loop (multi-asset balance aware). LIVE only — no paper/demo."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from tko.core.config import Settings
from tko.core.credentials import load_telegram, load_tokocrypto
from tko.core.types import Signal
from tko.exchange.tokocrypto import TokocryptoClient
from tko.execution.engine import ExecutionEngine
from tko.notify.telegram import TelegramNotifier
from tko.risk.engine import RiskEngine
from tko.strategy.btc import BtcAnalyzer

logger = logging.getLogger(__name__)

STABLE_LIKE = frozenset({"IDR", "USDT", "USDC", "BUSD", "USD", "BNB"})


class TradingBot:
    def __init__(self, settings: Settings, state_dir: Path) -> None:
        self.s = settings
        self.state_dir = state_dir
        creds = load_tokocrypto()
        self.client = TokocryptoClient(creds)
        self.risk = RiskEngine(settings, state_dir)
        self.strategy = BtcAnalyzer(settings)
        self.execution = ExecutionEngine(self.client, settings, state_dir)
        self.notify = TelegramNotifier(load_telegram() if settings.telegram_enabled else None)
        self._running = False

    def start(self) -> None:
        self._running = True
        self.client.connect()
        self.notify.send("TKO bot started — LIVE mode only (no paper/demo)")
        logger.info("Bot LIVE started — loop every %.0fs", self.s.loop_interval_sec)
        try:
            while self._running:
                if self.risk.kill_switch_active():
                    logger.warning("Kill switch active — sleeping")
                    self.notify.send("TKO: kill switch ACTIVE — not trading")
                    time.sleep(self.s.loop_interval_sec)
                    continue
                if self.client.circuit_open:
                    logger.critical("Circuit breaker open — not trading")
                    self.notify.send("TKO: exchange circuit breaker OPEN")
                    time.sleep(self.s.loop_interval_sec)
                    continue
                try:
                    self.execution.reconcile_pending()
                    self._tick()
                except Exception as exc:
                    logger.exception("Tick error: %s", exc)
                    if self.s.telegram_notify_on_error:
                        self.notify.send(f"TKO error: {type(exc).__name__}: {exc}")
                time.sleep(self.s.loop_interval_sec)
        finally:
            self.client.close()
            self.notify.send("TKO bot stopped")

    def stop(self) -> None:
        self._running = False

    def _tick(self) -> None:
        balances = self.client.fetch_balance()
        free_map = {a: b.free for a, b in balances.items() if b.free > 0}
        logger.info("balances free=%s", {k: round(v, 8) for k, v in sorted(free_map.items())})
        if self._manage_positions(free_map):
            return
        self._try_buy_primary(free_map)

    def _manage_positions(self, free_map: dict[str, float]) -> bool:
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
            if not symbol:
                symbol = self._find_any_market_for_base(base)
            if not symbol:
                logger.warning("No sell market for free %s=%.8f", base, free_base)
                continue
            ok, reason = self.client.validate_symbol_ready(symbol)
            if not ok:
                logger.warning("Symbol %s not ready: %s", symbol, reason)
                continue
            ticker = self.client.fetch_ticker(symbol)
            candles = self.client.fetch_ohlcv(
                symbol, timeframe=self.s.ohlcv_timeframe, limit=self.s.ohlcv_limit
            )
            decision = self.strategy.analyze(candles)
            pos = self.execution.positions.get(symbol)
            entry = pos.entry_price if pos else None
            sell_dec = self.risk.evaluate_sell(
                free_base=free_base,
                entry_price=entry,
                last_price=ticker.last,
                signal_sell=(decision.signal == Signal.SELL),
            )
            logger.info(
                "position %s free=%.8f last=%.4f signal=%s sell_approved=%s (%s)",
                symbol, free_base, ticker.last, decision.signal.value,
                sell_dec.approved, sell_dec.reason,
            )
            if sell_dec.approved:
                result = self.execution.sell(
                    symbol, sell_dec, ticker.last, base=base, quote=quote_used or "",
                )
                if result and self.s.telegram_notify_on_trade:
                    self.notify.send(
                        f"SELL {symbol}\namount={result.filled:.8f}\navg={result.average}\nreason={sell_dec.reason}"
                    )
                acted = True
        return acted

    def _try_buy_primary(self, free_map: dict[str, float]) -> None:
        base = self.s.base_asset.upper()
        quotes = self.s.quote_asset_list()
        open_pos = sum(
            1 for a, v in free_map.items()
            if a in self.s.tradeable_base_list()
            and a not in STABLE_LIKE
            and v > self.s.min_base_dust
        )
        for quote in quotes:
            free_quote = free_map.get(quote, 0.0)
            min_q = self.s.min_balance_for_quote(quote)
            if free_quote < min_q:
                continue
            symbol = self.client.resolve_symbol(base, quote)
            if not symbol:
                continue
            ok, reason = self.client.validate_symbol_ready(symbol)
            if not ok:
                logger.warning("Symbol %s not ready for buy: %s", symbol, reason)
                continue
            ticker = self.client.fetch_ticker(symbol)
            candles = self.client.fetch_ohlcv(
                symbol, timeframe=self.s.ohlcv_timeframe, limit=self.s.ohlcv_limit
            )
            decision = self.strategy.analyze(candles)
            logger.info(
                "tick %s last=%.4f free_%s=%.4f signal=%s (%s)",
                symbol, ticker.last, quote, free_quote,
                decision.signal.value, decision.reason,
            )
            if decision.signal != Signal.BUY:
                continue
            buy_dec = self.risk.evaluate_buy(
                free_quote=free_quote,
                last_price=ticker.last,
                open_positions=open_pos,
                quote_asset=quote,
            )
            if not buy_dec.approved:
                logger.info("buy skipped: %s", buy_dec.reason)
                continue
            result = self.execution.buy(
                symbol=symbol, base=base, quote=quote,
                decision=buy_dec, last_price=ticker.last,
            )
            if result and self.s.telegram_notify_on_trade:
                self.notify.send(
                    f"BUY {symbol}\namount={result.filled:.8f}\navg={result.average}\n"
                    f"spent≈{buy_dec.size_quote:.4f} {quote}\nreason={buy_dec.reason}"
                )
            return

    def _find_any_market_for_base(self, base: str) -> str | None:
        for q in self.s.quote_asset_list():
            s = self.client.resolve_symbol(base, q)
            if s:
                return s
        return None
