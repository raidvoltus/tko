"""Application configuration (no secrets). LIVE only — no paper/demo/dry-run."""

from __future__ import annotations

import math
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SettingsError(ValueError):
    """Invalid or unsafe configuration — fail closed before trading."""


class Settings(BaseSettings):
    """LIVE trading settings. Validated on load; unsafe values raise SettingsError."""

    model_config = SettingsConfigDict(
        env_prefix="TKO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    live_mode: bool = True

    exchange_id: str = "tokocrypto"
    account_id: str = "default"
    base_asset: str = "BTC"
    quote_asset: str = "IDR"
    quote_assets: str = "IDR,USDT,USDC"
    tradeable_bases: str = "BTC,ETH,BNB,SOL,XRP,ADA,DOGE,MATIC,AVAX,DOT,LINK,TRX"
    min_base_dust: float = Field(default=1e-8, gt=0, le=1.0)

    loop_interval_sec: float = Field(default=60.0, ge=5.0, le=3600.0)
    min_quote_balance: float = Field(default=50_000.0, gt=0)
    min_quote_balance_usdt: float = Field(default=5.0, gt=0)
    max_position_pct: float = Field(default=15.0, gt=0, le=100.0)
    take_profit_pct: float = Field(default=1.5, gt=0, le=100.0)
    stop_loss_pct: float = Field(default=2.0, gt=0, le=100.0)
    rsi_period: int = Field(default=14, ge=2, le=200)
    rsi_oversold: float = Field(default=35.0, ge=0.0, le=100.0)
    rsi_overbought: float = Field(default=70.0, ge=0.0, le=100.0)
    ema_fast: int = Field(default=9, ge=1, le=500)
    ema_slow: int = Field(default=21, ge=2, le=500)
    ohlcv_timeframe: str = "15m"
    ohlcv_limit: int = Field(default=100, ge=20, le=1000)
    # Stage 5 ML foundation (optional; default OFF — rule-based remains primary)
    ml_filter_enabled: bool = False
    ml_min_confidence: float = Field(default=0.55, ge=0.5, le=0.99)
    ml_model_path: str = ""
    ohlcv_store_enabled: bool = True
    # Stage 5.1 diversity / pool / ensemble / governor (default OFF)
    ml_governor_enabled: bool = False
    ml_profile: str = "ULTRA_LITE"
    ml_pool_max_models: int = Field(default=5, ge=1, le=7)
    ml_n_jobs: int = Field(default=1, ge=1, le=1)
    ml_max_trees: int = Field(default=40, ge=1, le=50)
    ml_max_depth: int = Field(default=4, ge=1, le=4)
    ml_max_training_rows: int = Field(default=3000, ge=100, le=5000)
    ml_safe_exit_on_pressure: bool = True
    ml_diversity_threshold: float = Field(default=0.80, ge=0.5, le=1.0)

    max_daily_loss_pct: float = Field(default=5.0, gt=0, le=100.0)
    max_open_positions: int = Field(default=3, ge=1, le=50)
    kill_switch_file: str = "state/KILL"

    max_order_notional: float = Field(default=7_500_000.0, gt=0)
    max_daily_notional: float = Field(default=30_000_000.0, gt=0)
    daily_equity_baseline: float = Field(default=0.0, ge=0.0)
    risk_timezone: str = "Asia/Jakarta"

    data_dir: str = "data"
    state_dir: str = "state"
    log_dir: str = "logs"
    log_level: str = "INFO"

    telegram_enabled: bool = True
    telegram_notify_on_trade: bool = True
    telegram_notify_on_error: bool = True

    reconcile_interval_sec: float = Field(default=900.0, ge=30.0, le=86_400.0)
    heartbeat_stale_sec: float = Field(default=300.0, ge=30.0, le=86_400.0)
    watchdog_enabled: bool = True
    telegram_kill_command: bool = True

    @field_validator(
        "min_base_dust",
        "loop_interval_sec",
        "min_quote_balance",
        "min_quote_balance_usdt",
        "max_position_pct",
        "take_profit_pct",
        "stop_loss_pct",
        "rsi_oversold",
        "rsi_overbought",
        "max_daily_loss_pct",
        "max_order_notional",
        "max_daily_notional",
        "daily_equity_baseline",
        "reconcile_interval_sec",
        "heartbeat_stale_sec",
        "ml_min_confidence",
        mode="after",
    )
    @classmethod
    def _finite_float(cls, v: float) -> float:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError("must be a real number")
        fv = float(v)
        if not math.isfinite(fv):
            raise ValueError("must be finite (not NaN/inf)")
        return fv

    @field_validator("log_level", mode="after")
    @classmethod
    def _log_level_ok(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        u = str(v).upper()
        if u not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return u

    @field_validator("ohlcv_timeframe", mode="after")
    @classmethod
    def _timeframe_ok(cls, v: str) -> str:
        s = str(v).strip()
        if not s or len(s) > 16:
            raise ValueError("invalid ohlcv_timeframe")
        return s

    @field_validator("base_asset", "quote_asset", mode="after")
    @classmethod
    def _asset_ok(cls, v: str) -> str:
        s = str(v).strip().upper()
        if not s or len(s) > 16 or not s.isalnum():
            raise ValueError("asset symbol must be alphanumeric 1-16 chars")
        return s

    @field_validator("exchange_id", mode="after")
    @classmethod
    def _exchange_tokocrypto(cls, v: str) -> str:
        s = str(v).strip().lower()
        if s != "tokocrypto":
            raise ValueError("exchange_id must be 'tokocrypto' (LIVE-only TKO bot)")
        return s

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Self:
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be < ema_slow")
        if self.rsi_oversold >= self.rsi_overbought:
            raise ValueError("rsi_oversold must be < rsi_overbought")
        if self.max_order_notional > self.max_daily_notional:
            raise ValueError("max_order_notional must be <= max_daily_notional")
        if self.heartbeat_stale_sec < self.loop_interval_sec:
            raise ValueError("heartbeat_stale_sec must be >= loop_interval_sec")
        if self.live_mode is not True:
            raise ValueError(
                "live_mode must be True. TKO is LIVE-only; paper/demo/dry-run are not supported. "
                "Unset TKO_LIVE_MODE or set TKO_LIVE_MODE=true."
            )
        return self

    def validate_for_live(self) -> None:
        try:
            Settings.model_validate(self.model_dump())
        except Exception as exc:
            raise SettingsError(f"Invalid configuration: {exc}") from exc
        if self.live_mode is not True:
            raise SettingsError("LIVE gate failed: live_mode is not True")
        if self.exchange_id != "tokocrypto":
            raise SettingsError("LIVE gate failed: exchange_id must be tokocrypto")
        if self.max_order_notional <= 0 or self.max_daily_notional <= 0:
            raise SettingsError("LIVE gate failed: notional caps must be positive")
        if self.max_daily_loss_pct <= 0:
            raise SettingsError("LIVE gate failed: max_daily_loss_pct must be positive")
        if self.loop_interval_sec < 5.0:
            raise SettingsError("LIVE gate failed: loop_interval_sec too small")

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
        if not out:
            raise SettingsError("quote_assets resolved to empty list")
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
        if not out:
            raise SettingsError("tradeable_bases resolved to empty list")
        return out

    def min_balance_for_quote(self, quote: str) -> float:
        q = quote.upper()
        if q in ("USDT", "USDC", "BUSD", "USD"):
            return float(self.min_quote_balance_usdt)
        return float(self.min_quote_balance)


def load_settings() -> Settings:
    try:
        settings = Settings()
    except Exception as exc:
        raise SettingsError(f"Failed to load settings: {exc}") from exc
    settings.validate_for_live()
    return settings
