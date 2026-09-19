"""Authoritative Tokocrypto account state — REST reconciliation + optional stream updates.

No PAPER/virtual balances. UNKNOWN must not be treated as zero for trading.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class AccountStatus(str, Enum):
    UNRECONCILED = "UNRECONCILED"
    RECONCILING = "RECONCILING"
    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


@dataclass
class AssetBalance:
    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal
    valuation_price: Optional[Decimal] = None
    valuation_usdt: Optional[Decimal] = None
    valuation_timestamp: Optional[float] = None
    state: str = "OK"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset": self.asset,
            "free": str(self.free),
            "locked": str(self.locked),
            "total": str(self.total),
            "valuation_price": str(self.valuation_price) if self.valuation_price is not None else None,
            "valuation_usdt": str(self.valuation_usdt) if self.valuation_usdt is not None else None,
            "valuation_timestamp": self.valuation_timestamp,
            "state": self.state,
        }


@dataclass
class AccountState:
    status: AccountStatus = AccountStatus.UNRECONCILED
    source: str = ""
    reconciled_at: float = 0.0
    last_exchange_event_at: float = 0.0
    assets: Dict[str, AssetBalance] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    raw_asset_count: int = 0

    def is_tradeable(self) -> bool:
        return self.status == AccountStatus.VALID

    def free(self, asset: str) -> Optional[Decimal]:
        a = self.assets.get(asset.upper())
        return a.free if a else None

    def total(self, asset: str) -> Optional[Decimal]:
        a = self.assets.get(asset.upper())
        return a.total if a else None

    def holdings(self) -> List[AssetBalance]:
        return [a for a in self.assets.values() if a.total > 0]

    def available_quote(self, quote: str = "USDT") -> Optional[Decimal]:
        """Trading available = free only (never total/locked)."""
        if self.status != AccountStatus.VALID:
            return None
        a = self.assets.get(quote.upper())
        return a.free if a else Decimal("0")

    def equity_usdt(self) -> Tuple[Optional[Decimal], List[str]]:
        """Sum valued holdings; return (equity_or_None, unpriced_assets)."""
        if self.status not in (AccountStatus.VALID, AccountStatus.STALE):
            return None, list(self.assets.keys())
        total = Decimal("0")
        unpriced: List[str] = []
        for a in self.holdings():
            if a.asset in ("USDT", "USDC", "BUSD"):
                total += a.total
            elif a.valuation_usdt is not None:
                total += a.valuation_usdt
            else:
                unpriced.append(a.asset)
        return total, unpriced

    def to_dict(self) -> Dict[str, Any]:
        eq, unpriced = self.equity_usdt()
        usdt = self.assets.get("USDT")
        return {
            "status": self.status.value,
            "source": self.source,
            "reconciled_at": self.reconciled_at,
            "last_exchange_event_at": self.last_exchange_event_at,
            "raw_asset_count": self.raw_asset_count,
            "errors": list(self.errors),
            "tradeable": self.is_tradeable(),
            "available_usdt": str(usdt.free) if usdt else None,
            "locked_usdt": str(usdt.locked) if usdt else None,
            "total_usdt": str(usdt.total) if usdt else None,
            "equity_usdt": str(eq) if eq is not None else None,
            "unpriced_assets": unpriced,
            "assets": {k: v.to_dict() for k, v in sorted(self.assets.items())},
            "holdings": [v.to_dict() for v in self.holdings()],
        }


def _dec(v: Any) -> Tuple[Optional[Decimal], Optional[str]]:
    if v is None:
        return Decimal("0"), None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None, f"non_decimal:{v!r}"
    if d.is_nan() or d.is_infinite():
        return None, f"non_finite:{v!r}"
    if d < 0:
        return None, f"negative:{v!r}"
    return d, None


def parse_account_assets(body: Dict[str, Any]) -> Tuple[Dict[str, AssetBalance], List[str], int]:
    """
    Parse Tokocrypto GET /open/v1/account/spot body.
    Prefer data.accountAssets[]; also accept balances/list shapes.
    Returns (assets, errors, raw_count). Fail-closed on structural issues.
    """
    errors: List[str] = []
    if not isinstance(body, dict):
        return {}, ["body_not_dict"], 0

    raw_list = None
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("accountAssets", "balances", "balance", "list", "spotBalances", "assets"):
            v = data.get(key)
            if isinstance(v, list):
                raw_list = v
                break
    if raw_list is None and isinstance(data, list):
        raw_list = data
    if raw_list is None:
        for key in ("accountAssets", "balances", "balance", "list"):
            v = body.get(key)
            if isinstance(v, list):
                raw_list = v
                break
    if raw_list is None:
        return {}, ["missing_accountAssets"], 0

    assets: Dict[str, AssetBalance] = {}
    for i, row in enumerate(raw_list):
        if not isinstance(row, dict):
            errors.append(f"row_{i}_not_dict")
            continue
        asset = str(row.get("asset") or row.get("coin") or row.get("currency") or "").strip().upper()
        if not asset or not asset.isalnum():
            errors.append(f"row_{i}_invalid_asset:{asset!r}")
            continue
        free, e1 = _dec(row.get("free", row.get("available", row.get("avail"))))
        locked, e2 = _dec(row.get("locked", row.get("freeze", row.get("frozen"))))
        if e1 or e2:
            errors.append(f"{asset}:{e1 or e2}")
            continue
        assert free is not None and locked is not None
        total = free + locked
        assets[asset] = AssetBalance(asset=asset, free=free, locked=locked, total=total)
    return assets, errors, len(raw_list)


class AccountReconciler:
    """REST snapshot → AccountState. Optional incremental reduce later."""

    def __init__(self, stale_after_sec: float = 120.0):
        self.stale_after_sec = stale_after_sec
        self.state = AccountState()

    def begin(self) -> None:
        self.state.status = AccountStatus.RECONCILING
        self.state.errors = []

    def apply_rest_snapshot(self, body: Dict[str, Any], *, source: str = "REST_ACCOUNT_SPOT") -> AccountState:
        self.begin()
        assets, errors, raw_n = parse_account_assets(body)
        self.state.raw_asset_count = raw_n
        self.state.errors = errors
        self.state.source = source
        self.state.reconciled_at = time.time()
        self.state.last_exchange_event_at = self.state.reconciled_at
        # Structural failure: missing list entirely
        if "missing_accountAssets" in errors and raw_n == 0 and not assets:
            self.state.status = AccountStatus.INVALID
            self.state.assets = {}
            return self.state
        # Malformed rows recorded but if we got any valid assets keep VALID
        # Policy: any negative/NaN row → still keep other assets but flag errors;
        # if ALL rows failed and raw_n>0 → INVALID
        if raw_n > 0 and not assets and errors:
            self.state.status = AccountStatus.INVALID
            self.state.assets = {}
            return self.state
        self.state.assets = assets
        self.state.status = AccountStatus.VALID
        return self.state

    def mark_failed(self, reason: str) -> AccountState:
        self.state.status = AccountStatus.UNKNOWN
        self.state.errors = [reason]
        self.state.source = "FETCH_FAILED"
        # do not invent zeros — clear assets so trading cannot use stale-as-zero
        self.state.assets = {}
        self.state.reconciled_at = time.time()
        return self.state

    def mark_stale(self, reason: str = "user_stream_disconnect") -> AccountState:
        if self.state.status == AccountStatus.VALID:
            self.state.status = AccountStatus.STALE
        else:
            self.state.status = AccountStatus.UNKNOWN
        self.state.errors.append(reason)
        return self.state

    def apply_user_stream_balance(self, asset: str, free: Any, locked: Any, event_ts: float) -> AccountState:
        """Incremental update; reject out-of-order / malformed."""
        if self.state.status not in (AccountStatus.VALID, AccountStatus.STALE):
            self.state.errors.append("stream_update_while_not_reconciled")
            return self.state
        if event_ts and self.state.last_exchange_event_at and event_ts < self.state.last_exchange_event_at - 1.0:
            self.state.errors.append("out_of_order_event")
            return self.state
        asset_u = str(asset).upper()
        free_d, e1 = _dec(free)
        locked_d, e2 = _dec(locked)
        if e1 or e2 or free_d is None or locked_d is None:
            self.state.errors.append(f"stream_malformed:{asset_u}")
            return self.state
        self.state.assets[asset_u] = AssetBalance(
            asset=asset_u, free=free_d, locked=locked_d, total=free_d + locked_d
        )
        if event_ts:
            self.state.last_exchange_event_at = max(self.state.last_exchange_event_at, float(event_ts))
        return self.state

    def apply_valuations(self, prices_usdt: Dict[str, float]) -> None:
        now = time.time()
        for asset, bal in self.state.assets.items():
            if asset in ("USDT", "USDC", "BUSD"):
                bal.valuation_price = Decimal("1")
                bal.valuation_usdt = bal.total
                bal.valuation_timestamp = now
                continue
            px = prices_usdt.get(asset)
            if px is None or not (px == px) or px <= 0 or px == float("inf"):
                bal.valuation_price = None
                bal.valuation_usdt = None
                bal.valuation_timestamp = None
                continue
            dpx = Decimal(str(px))
            bal.valuation_price = dpx
            bal.valuation_usdt = bal.total * dpx
            bal.valuation_timestamp = now
