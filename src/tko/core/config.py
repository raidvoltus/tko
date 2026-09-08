"""Application configuration (no secrets)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Secrets live in CredentialStore only."""

    model_config = SettingsConfigDict(
        env_prefix="TKO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Exchange
    exchange_id: str = "tokocrypto"
    account_id: str = "default"
    quote_asset: str = "IDR"  # primary quote for balance check
    base_asset: str = "BTC"  # primary analysis / trade target

    # Strategy
    loop_interval_sec: float = 60.0
    min_quote_balance: float = 50_000.0  # min IDR (or quote) to consider buying
    max_position_pct: float = 15.0  # % of free quote balance per entry
    take_profit_pct: float = 1.5  # sell when +1.5%
    stop_loss_pct: float = 2.0  # sell when -2.0%
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_overbought: float = 70.0
    ema_fast: int = 9
    ema_slow: int = 21
    ohlcv_timeframe: str = "15m"
    ohlcv_limit: int = 100

    # Risk
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 3
    kill_switch_file: str = "state/KILL"

    # Runtime
    data_dir: str = "data"
    state_dir: str = "state"
    log_dir: str = "logs"
    log_level: str = "INFO"
    dry_run: bool = False  # if True, still LIVE path but skip create_order (emergency)

    # Telegram (token/chat stored in credentials; these are flags)
    telegram_enabled: bool = True
    telegram_notify_on_trade: bool = True
    telegram_notify_on_error: bool = True


def load_settings() -> Settings:
    return Settings()
