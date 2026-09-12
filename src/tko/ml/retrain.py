"""Autonomous retraining scheduler — conservative, gate-driven."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from tko.core.types import OHLCV
from tko.ml.registry import ModelRegistry, ModelState
from tko.ml.train_pipeline import TrainConfig, TrainReport, run_training

logger = logging.getLogger(__name__)


@dataclass
class RetrainPolicy:
    min_new_bars: int = 100
    max_model_age_sec: float = 30 * 24 * 3600
    min_interval_sec: float = 24 * 3600


@dataclass
class RetrainState:
    last_run_ts: float = 0.0
    last_data_end_ms: int = 0
    last_decision: str = ""


def should_retrain(
    *,
    state: RetrainState,
    latest_data_end_ms: int,
    policy: RetrainPolicy | None = None,
    now: float | None = None,
) -> tuple[bool, str]:
    pol = policy or RetrainPolicy()
    now_ts = float(now if now is not None else time.time())
    if state.last_run_ts and (now_ts - state.last_run_ts) < pol.min_interval_sec:
        return False, "min_interval"
    # rough bar count proxy via ms gap (15m default)
    if state.last_data_end_ms and latest_data_end_ms:
        gap_bars = max(0, (latest_data_end_ms - state.last_data_end_ms) // 900_000)
        if gap_bars < pol.min_new_bars and state.last_run_ts:
            age = now_ts - state.last_run_ts
            if age < pol.max_model_age_sec:
                return False, "insufficient_new_data"
    return True, "ok"


def run_retrain_cycle(
    candles: list[OHLCV],
    *,
    registry_dir: Path,
    artifact_root: Path,
    state: RetrainState,
    config: TrainConfig | None = None,
    policy: RetrainPolicy | None = None,
) -> tuple[TrainReport, RetrainState]:
    """Idempotent retrain cycle. Never activates production ML filter."""
    end_ms = int(candles[-1].timestamp_ms) if candles else 0
    ok, reason = should_retrain(state=state, latest_data_end_ms=end_ms, policy=policy)
    if not ok:
        report = TrainReport(run_id="skipped", decision="REJECTED", notes=[f"skip:{reason}"])
        return report, state
    report = run_training(
        candles,
        registry_dir=registry_dir,
        artifact_root=artifact_root,
        config=config,
    )
    state.last_run_ts = time.time()
    state.last_data_end_ms = end_ms
    state.last_decision = report.decision
    logger.info(
        "event=retrain_cycle decision=%s run_id=%s",
        report.decision,
        report.run_id,
    )
    return report, state


def deactivate_on_degradation(
    registry: ModelRegistry,
    model_id: str,
    *,
    reason: str = "degradation",
) -> None:
    """Rollback path: ACTIVE/SHADOW → REJECTED, clear active pointer."""
    rec = registry.latest(model_id)
    if rec is None:
        return
    st = ModelState(rec.state)
    if st == ModelState.ACTIVE:
        registry.transition(model_id, ModelState.REJECTED, notes=reason)
        registry.clear_active()
    elif st == ModelState.SHADOW:
        registry.transition(model_id, ModelState.REJECTED, notes=reason)
