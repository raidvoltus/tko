"""Readiness vs liveness — trading requires readiness, not mere process alive."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    live: bool
    ready: bool
    reasons: tuple[str, ...]

    @property
    def trading_allowed(self) -> bool:
        return self.live and self.ready and not self.reasons


def evaluate_readiness(
    *,
    process_alive: bool,
    exchange_ok: bool,
    market_data_fresh: bool,
    user_stream_healthy: bool,
    recon_ok: bool,
    risk_healthy: bool,
    kill_switch_off: bool,
    require_user_stream: bool = False,
    require_ws_market: bool = False,
) -> ReadinessReport:
    """NO TRADE unless all required gates pass."""
    reasons: list[str] = []
    if not process_alive:
        reasons.append("process_not_alive")
    if not exchange_ok:
        reasons.append("exchange_not_ok")
    if require_ws_market and not market_data_fresh:
        reasons.append("market_data_stale")
    if require_user_stream and not user_stream_healthy:
        reasons.append("user_stream_unhealthy")
    if not recon_ok:
        reasons.append("recon_not_ok")
    if not risk_healthy:
        reasons.append("risk_unhealthy")
    if not kill_switch_off:
        reasons.append("kill_switch_active")
    ready = len(reasons) == 0
    return ReadinessReport(live=process_alive, ready=ready, reasons=tuple(reasons))
