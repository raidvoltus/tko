"""Tests for ComputationalGovernor — Stage 5.1."""

from __future__ import annotations

from tko.ml.governor import (
    ComputationalGovernor,
    GovernorConfig,
    GovernorLevel,
    ResourceSnapshot,
)


def test_disabled_always_full() -> None:
    gov = ComputationalGovernor(GovernorConfig(enabled=False))
    gov.observe(ResourceSnapshot(ram_used_pct=0.99, latency_ms=999.0))
    assert gov.current_level() is GovernorLevel.FULL
    assert not gov.should_hold_only()
    assert len(gov.allowed_models(["a", "b", "c"])) == 3


def test_full_when_resources_ok() -> None:
    gov = ComputationalGovernor(GovernorConfig(enabled=True, max_models=5))
    gov.observe(ResourceSnapshot(ram_used_pct=0.40, latency_ms=50.0))
    assert gov.current_level() is GovernorLevel.FULL
    assert not gov.should_hold_only()
    names = gov.allowed_models(["m1", "m2", "m3", "m4", "m5", "m6"])
    assert len(names) == 5


def test_degrade_1_on_ram_warn() -> None:
    gov = ComputationalGovernor(
        GovernorConfig(enabled=True, ram_warn_pct=0.80, ram_crit_pct=0.92)
    )
    gov.observe(ResourceSnapshot(ram_used_pct=0.85, latency_ms=50.0))
    assert gov.current_level() is GovernorLevel.DEGRADE_1
    assert not gov.should_hold_only()


def test_safe_exit_on_ram_crit() -> None:
    gov = ComputationalGovernor(
        GovernorConfig(enabled=True, ram_crit_pct=0.92, safe_exit_on_pressure=True)
    )
    gov.observe(ResourceSnapshot(ram_used_pct=0.95, latency_ms=50.0))
    assert gov.current_level() is GovernorLevel.SAFE_EXIT
    assert gov.should_hold_only()
    assert gov.allowed_models(["a", "b"]) == []


def test_safe_exit_on_latency_crit() -> None:
    gov = ComputationalGovernor(
        GovernorConfig(
            enabled=True,
            latency_crit_ms=600.0,
            safe_exit_on_pressure=True,
        )
    )
    gov.observe(ResourceSnapshot(ram_used_pct=0.30, latency_ms=700.0))
    assert gov.current_level() is GovernorLevel.SAFE_EXIT
    assert gov.should_hold_only()


def test_probe_error_does_not_crash() -> None:
    """Even with bad probe the governor must stay usable."""
    gov = ComputationalGovernor(GovernorConfig(enabled=True))
    # Calling observe without snap still works (internal probe may fail)
    level = gov.observe()
    assert level in GovernorLevel
    assert isinstance(gov.should_hold_only(), bool)


def test_allowed_models_respects_level_and_cap() -> None:
    gov = ComputationalGovernor(GovernorConfig(enabled=True, max_models=3))
    gov.observe(ResourceSnapshot(ram_used_pct=0.40, latency_ms=10.0))
    assert gov.current_level() is GovernorLevel.FULL
    names = gov.allowed_models(["a", "b", "c", "d", "e"])
    assert len(names) == 3
    assert names == ["a", "b", "c"]
