"""Main LIVE trading loop."""

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


class TradingBot:
    def __init__(self, settings: Settings, state_dir: Path) -> None:
        self.s = settings
        self.state_dir = state_dir
        creds = load_tokocrypto()
        self.client = TokocryptoClient(creds)
        self.risk = RiskEngine(settings, state_dir)
        self.strategy = BtcAnalyzer(settings)
        self.execution = ExecutionEngine(self.client, settings)
        self.notify = TelegramNotifier(load_telegram() if settings.telegram_enabled else None)
        self._running = False

    def start(self) -> None:
        self._running = True
        self.client.connect()
        self.notify.send("TKO bot started (LIVE mode)")
        logger.info("Bot LIVE started — loop every %.0fs", self.s.loop_interval_sec)
        try:
            while self._running:
                if self.risk.kill_switch_active():
                    logger.warning("Kill switch active — sleeping")
                    self.notify.send("TKO: kill switch ACTIVE — not trading")
                    time.sleep(self.s.loop_interval_sec)
                    continue
                try:
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
        quote = self.s.quote_asset.upper()
        base = self.s.base_asset.upper()

        symbol = self.client.resolve_symbol(base, quote)
        if not symbol:
            symbol = self.client.resolve_symbol(base, "USDT")
        if not symbol:
            logger.error("No market for %s/%s", base, quote)
            return

        balances = self.client.fetch_balance()
        free_quote = balances.get(quote).free if quote in balances else 0.0
        free_base = balances.get(base).free if base in balances else 0.0

        ticker = self.client.fetch_ticker(symbol)
        candles = self.client.fetch_ohlcv(
            symbol, timeframe=self.s.ohlcv_timeframe, limit=self.s.ohlcv_limit
        )
        decision = self.strategy.analyze(candles)
        logger.info(
            "tick %s last=%.2f free_%s=%.0f free_%s=%.8f signal=%s (%s)",
            symbol,
            ticker.last,
            quote,
            free_quote,
            base,
            free_base,
            decision.signal.value,
            decision.reason,
        )

        open_pos = 1 if free_base > 0 else 0
        pos = self.execution.positions.get(symbol)
        entry = pos.entry_price if pos else None

        if free_base > 0:
            sell_dec = self.risk.evaluate_sell(
                free_base=free_base,
                entry_price=entry,
                last_price=ticker.last,
                signal_sell=(decision.signal == Signal.SELL),
            )
            if sell_dec.approved:
                result = self.execution.sell(symbol, sell_dec, ticker.last)
                if result and self.s.telegram_notify_on_trade:
                    self.notify.send(
                        f"SELL {symbol}\n"
                        f"amount={result.filled:.8f}\n"
                        f"avg={result.average}\n"
                        f"reason={sell_dec.reason}"
                    )
            return

        if decision.signal == Signal.BUY:
            buy_dec = self.risk.evaluate_buy(
                free_quote=free_quote,
                last_price=ticker.last,
                open_positions=open_pos,
            )
            if buy_dec.approved:
                result = self.execution.buy(
                    symbol=symbol,
                    base=base,
                    quote=quote,
                    decision=buy_dec,
                    last_price=ticker.last,
                )
                if result and self.s.telegram_notify_on_trade:
                    self.notify.send(
                        f"BUY {symbol}\n"
                        f"amount={result.filled:.8f}\n"
                        f"avg={result.average}\n"
                        f"spent≈{buy_dec.size_quote:.0f} {quote}\n"
                        f"reason={buy_dec.reason}"
                    )
