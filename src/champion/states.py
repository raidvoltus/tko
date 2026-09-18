"""Challenger lifecycle states — no direct jump to production."""
from __future__ import annotations

from enum import Enum


class ChallengerState(str, Enum):
    CANDIDATE = "CANDIDATE"
    BACKTESTED = "BACKTESTED"
    VALIDATED = "VALIDATED"
    SHADOW_EVALUATED = "SHADOW_EVALUATED"
    ELIGIBLE = "ELIGIBLE"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    INVALID = "INVALID"
    ROLLED_BACK = "ROLLED_BACK"


# Legal transitions (conservative)
ALLOWED_TRANSITIONS = {
    ChallengerState.CANDIDATE: {ChallengerState.BACKTESTED, ChallengerState.REJECTED, ChallengerState.INVALID},
    ChallengerState.BACKTESTED: {ChallengerState.VALIDATED, ChallengerState.REJECTED, ChallengerState.DEGRADED},
    ChallengerState.VALIDATED: {
        ChallengerState.SHADOW_EVALUATED,
        ChallengerState.REJECTED,
        ChallengerState.DEGRADED,
    },
    ChallengerState.SHADOW_EVALUATED: {
        ChallengerState.ELIGIBLE,
        ChallengerState.REJECTED,
        ChallengerState.DEGRADED,
        ChallengerState.STALE,
    },
    ChallengerState.ELIGIBLE: {
        ChallengerState.PROMOTED,
        ChallengerState.REJECTED,
        ChallengerState.STALE,
        ChallengerState.DEGRADED,
    },
    ChallengerState.PROMOTED: {ChallengerState.ROLLED_BACK, ChallengerState.DEGRADED},
    ChallengerState.REJECTED: set(),
    ChallengerState.DEGRADED: {ChallengerState.REJECTED},
    ChallengerState.STALE: {ChallengerState.REJECTED},
    ChallengerState.INVALID: set(),
    ChallengerState.ROLLED_BACK: set(),
}


class PromotionDecision(str, Enum):
    KEEP_CHAMPION = "KEEP_CHAMPION"
    PROMOTE = "PROMOTE"
    REJECT = "REJECT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
