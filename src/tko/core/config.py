"""Application configuration (no secrets). LIVE only — no paper/demo/dry-run."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(Exception):
    """Fail-closed configuration error."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TKO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    live_mode: bool = True
    exchange_id: str = "tokocrypto"
    symbol: str = "BTC/IDR"
    quote_asset: str = "IDR"
    base_asset: str = "BTC"

    max_daily_loss_pct: float = Field(default=2.0, gt=0)
    max_order_notional: float = Field(default=5_000_000.0, gt=0)
    max_daily_notional: float = Field(default=50_000_000.0, gt=0)
    max_open_positions: int = Field(default=3, ge=0)
    min_quote_balance: float = Field(default=100_000.0, ge=0)
    equity_baseline: float = Field(default=0.0, ge=0)

    market_data_max_age_sec: float = Field(default=30.0, ge=0)
    heartbeat_stale_sec: float = Field(default=120.0, gt=0)
    recv_window_ms: int = Field(default=5000, gt=0)

    telegram_enabled: bool = False
    log_level: str = "INFO"

    # ML secondary filter — defaults OFF (Stage 5.1 core sovereign)
    ml_filter_enabled: bool = False
    ml_governor_enabled: bool = False
    ml_min_confidence: float = Field(default=0.55, ge=0.0, le=1.0)

    @field_validator("max_daily_loss_pct", "max_order_notional", "max_daily_notional", "min_quote_balance", "equity_baseline", "market_data_max_age_sec", "heartbeat_stale_sec", "ml_min_confidence", mode="before")
    @classmethod
    def _finite_float(cls, v: float) -> float:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise TypeError("must be a real number")
        fv = float(v)
        if not math.isfinite(fv):
            raise ValueError("must be finite")
        return fv

    def validate_for_live(self) -> None:
        if self.live_mode is not True:
            raise SettingsError("live_mode must be True")
        if self.exchange_id.lower() != "tokocrypto":
            raise SettingsError("exchange_id must be tokocrypto")
        if self.max_daily_loss_pct <= 0:
            raise SettingsError("max_daily_loss_pct must be > 0")
        if self.max_order_notional <= 0:
            raise SettingsError("max_order_notional must be > 0")
        if self.max_daily_notional <= 0:
            raise SettingsError("max_daily_notional must be > 0")


def load_settings() -> Settings:
    try:
        s = Settings()
    except Exception as exc:  # noqa: BLE001
        raise SettingsError(str(exc)) from exc
    if os.environ.get("TKO_LIVE_MODE", "true").lower() in ("0", "false", "no"):
        raise SettingsError("TKO_LIVE_MODE disables live trading")
    return s
