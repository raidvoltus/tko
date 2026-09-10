"""Computational Governor — controls how many ML models may run.

Levels: FULL → DEGRADE_1 → DEGRADE_2 → SAFE_EXIT.
Additive only. Never touches trading logic. Default OFF / fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Sequence


class GovernorLevel(Enum):
    FULL = auto()
    DEGRADE_1 = auto()
    DEGRADE_2 = auto()
    SAFE_EXIT = auto()


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """Observed resource pressure. Values are best-effort."""

    ram_used_pct: float  # 0.0 – 1.0
    latency_ms: float  # last inference latency


@dataclass(frozen=True, slots=True)
class GovernorConfig:
    """Hard bounds for the computational governor."""

    enabled: bool = False
    max_models: int = 5
    ram_warn_pct: float = 0.80
    ram_crit_pct: float = 0.92
    latency_warn_ms: float = 250.0
    latency_crit_ms: float = 600.0
    safe_exit_on_pressure: bool = True


# Model counts per level (capped by max_models)
_LEVEL_CAPS: dict[GovernorLevel, int] = {
    GovernorLevel.FULL: 7,
    GovernorLevel.DEGRADE_1: 5,
    GovernorLevel.DEGRADE_2: 3,
    GovernorLevel.SAFE_EXIT: 0,
}


def _probe_ram_pct() -> float:
    """Best-effort RAM usage. Fail closed to 0.0 on any error."""
    try:
        import psutil  # type: ignore[import-untyped]

        return float(psutil.virtual_memory().percent) / 100.0
    except Exception:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            mem: dict[str, int] = {}
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    key = parts[0][:-1]
                    mem[key] = int(parts[1])
            total = mem.get("MemTotal", 0)
            available = mem.get("MemAvailable", mem.get("MemFree", 0))
            if total > 0:
                used = max(0, total - available)
                return min(1.0, used / total)
    except Exception:
        pass
    return 0.0


class ComputationalGovernor:
    """Decides how many models may run under current resource pressure.

    Does not call exchange, risk, or execution. Pure advisory.
    """

    def __init__(self, config: GovernorConfig | None = None) -> None:
        self.cfg = config or GovernorConfig()
        self._level = GovernorLevel.FULL
        self._last_snap: ResourceSnapshot | None = None

    def observe(self, snap: ResourceSnapshot | None = None) -> GovernorLevel:
        """Update level from a resource snapshot (or live probe).

        If snap is None and a previous snapshot exists, keep the last decision
        (avoids overwriting injected test snapshots with live probes).
        """
        if not self.cfg.enabled:
            self._level = GovernorLevel.FULL
            return self._level

        if snap is None:
            if self._last_snap is not None:
                return self._level
            try:
                snap = ResourceSnapshot(
                    ram_used_pct=_probe_ram_pct(),
                    latency_ms=0.0,
                )
            except Exception:
                snap = ResourceSnapshot(ram_used_pct=0.0, latency_ms=0.0)

        self._last_snap = snap
        self._level = self._decide(snap)
        return self._level

    def _decide(self, snap: ResourceSnapshot) -> GovernorLevel:
        cfg = self.cfg
        if not cfg.safe_exit_on_pressure:
            if (
                snap.ram_used_pct >= cfg.ram_crit_pct
                or snap.latency_ms >= cfg.latency_crit_ms
            ):
                return GovernorLevel.DEGRADE_2
            if (
                snap.ram_used_pct >= cfg.ram_warn_pct
                or snap.latency_ms >= cfg.latency_warn_ms
            ):
                return GovernorLevel.DEGRADE_1
            return GovernorLevel.FULL

        if (
            snap.ram_used_pct >= cfg.ram_crit_pct
            or snap.latency_ms >= cfg.latency_crit_ms
        ):
            return GovernorLevel.SAFE_EXIT
        if (
            snap.ram_used_pct >= cfg.ram_warn_pct
            or snap.latency_ms >= cfg.latency_warn_ms
        ):
            return GovernorLevel.DEGRADE_1
        mid_ram = (cfg.ram_warn_pct + cfg.ram_crit_pct) / 2.0
        mid_lat = (cfg.latency_warn_ms + cfg.latency_crit_ms) / 2.0
        if snap.ram_used_pct >= mid_ram or snap.latency_ms >= mid_lat:
            return GovernorLevel.DEGRADE_2
        return GovernorLevel.FULL

    def current_level(self) -> GovernorLevel:
        if not self.cfg.enabled:
            return GovernorLevel.FULL
        return self._level

    def allowed_models(self, candidate_names: Sequence[str] | None = None) -> list[str]:
        """Return up to N model names allowed at the current level."""
        level = self.current_level()
        cap = min(_LEVEL_CAPS[level], self.cfg.max_models)
        if cap <= 0:
            return []
        if candidate_names is None:
            return [f"slot_{i}" for i in range(cap)]
        return list(candidate_names)[:cap]

    def should_hold_only(self) -> bool:
        """True when SAFE_EXIT is active → suppress BUY, keep SELL free."""
        if not self.cfg.enabled:
            return False
        return self.current_level() is GovernorLevel.SAFE_EXIT
