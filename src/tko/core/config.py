"""Application configuration (no secrets). LIVE only — no paper/demo/dry-run."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TKO_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    exchange_id: str = "tokocrypto"
    account_id: str = "default"
    base_asset: str = "BTC"
    quote_asset: str = "IDR"
    quote_assets: str = "IDR,USDT,USDC"
    tradeable_bases: str = "BTC,ETH,BNB,SOL,XRP,ADA,DOGE,MATIC,AVAX,DOT,LINK,TRX"
    min_base_dust: float = 1e-8

    loop_interval_sec: float = 60.0
    min_quote_balance: float = 50_000.0
    min_quote_balance_usdt: float = 5.0
    max_position_pct: float = 15.0
    take_profit_pct: float = 1.5
    stop_loss_pct: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_overbought: float = 70.0
    ema_fast: int = 9
    ema_slow: int = 21
    ohlcv_timeframe: str = "15m"
    ohlcv_limit: int = 100

    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 3
    kill_switch_file: str = "state/KILL"

    max_order_notional: float = 7_500_000.0
    max_daily_notional: float = 30_000_000.0
    daily_equity_baseline: float = 0.0
    risk_timezone: str = "Asia/Jakarta"

    data_dir: str = "data"
    state_dir: str = "state"
    log_dir: str = "logs"
    log_level: str = "INFO"

    telegram_enabled: bool = True
    telegram_notify_on_trade: bool = True
    telegram_notify_on_error: bool = True

    reconcile_interval_sec: float = 900.0
    heartbeat_stale_sec: float = 300.0
    watchdog_enabled: bool = True
    telegram_kill_command: bool = True

    def quote_asset_list(self) -> list[str]:
        items = [x.strip().upper() for x in self.quote_assets.split(",") if x.strip()]
        primary = self.quote_asset.upper()
        ordered = [primary] + [a for a in items if a != primary]
        seen: set[str] = set()
        out: list[str] = []
        for a in ordered:
            if a not in seen:
                seen.add(a)
                out.append(a)
        return out

    def tradeable_base_list(self) -> list[str]:
        items = [x.strip().upper() for x in self.tradeable_bases.split(",") if x.strip()]
        primary = self.base_asset.upper()
        if primary not in items:
            items.insert(0, primary)
        seen: set[str] = set()
        out: list[str] = []
        for a in items:
            if a not in seen:
                seen.add(a)
                out.append(a)
        return out

    def min_balance_for_quote(self, quote: str) -> float:
        q = quote.upper()
        if q in ("USDT", "USDC", "BUSD", "USD"):
            return float(self.min_quote_balance_usdt)
        return float(self.min_quote_balance)


def load_settings() -> Settings:
    return Settings()
