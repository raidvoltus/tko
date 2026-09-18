"""
Operational recovery state machine (crash → reconcile → resume/no-trade).

Does not place orders. Resume eligibility still requires Risk VALID + streams healthy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class RecoveryPhase(str, Enum):
    CRASHED = "CRASHED"
    RESTARTING = "RESTARTING"
    RECONCILING = "RECONCILING"
    REBUILD_STATE = "REBUILD_STATE"
    VERIFY_INVARIANTS = "VERIFY_INVARIANTS"
    READY = "READY"          # may trade only if Risk allows
    NO_TRADE = "NO_TRADE"    # fail-closed hold


@dataclass
class RecoveryCertificate:
    phase: str
    steps: List[str] = field(default_factory=list)
    invariants_ok: bool = False
    risk_state_valid: bool = False
    streams_healthy: bool = False
    eligible_to_trade: bool = False
    notes: List[str] = field(default_factory=list)


class RecoveryController:
    def __init__(self) -> None:
        self.phase = RecoveryPhase.CRASHED
        self._log: List[str] = []

    def begin_restart(self) -> None:
        self.phase = RecoveryPhase.RESTARTING
        self._log.append("restart")

    def begin_reconcile(self) -> None:
        self.phase = RecoveryPhase.RECONCILING
        self._log.append("reconcile_start")

    def mark_reconcile_failed(self) -> RecoveryCertificate:
        self.phase = RecoveryPhase.NO_TRADE
        self._log.append("reconcile_fail")
        return RecoveryCertificate(
            phase=self.phase.value,
            steps=list(self._log),
            eligible_to_trade=False,
            notes=["RECONCILE_FAILED"],
        )

    def rebuild_and_verify(
        self,
        *,
        risk_state_valid: bool,
        streams_healthy: bool,
        kill_switch: bool,
        circuit_breaker: bool,
        ledger_chain_ok: bool = True,
    ) -> RecoveryCertificate:
        self.phase = RecoveryPhase.REBUILD_STATE
        self._log.append("rebuild")
        self.phase = RecoveryPhase.VERIFY_INVARIANTS
        inv = (
            risk_state_valid
            and streams_healthy
            and not kill_switch
            and not circuit_breaker
            and ledger_chain_ok
        )
        self._log.append("verify")
        if inv:
            self.phase = RecoveryPhase.READY
            eligible = True
        else:
            self.phase = RecoveryPhase.NO_TRADE
            eligible = False
        return RecoveryCertificate(
            phase=self.phase.value,
            steps=list(self._log),
            invariants_ok=inv,
            risk_state_valid=risk_state_valid,
            streams_healthy=streams_healthy,
            eligible_to_trade=eligible,
            notes=[] if eligible else ["HOLD_NO_TRADE"],
        )
