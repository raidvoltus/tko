"""Model Pool — holds several lightweight backends under governor control.

Fail-closed: failed backends are skipped; SAFE_EXIT returns empty array.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tko.ml.backends.base import Backend
from tko.ml.governor import ComputationalGovernor


class ModelPool:
    """Pool of up to max_models backends, optionally governed."""

    def __init__(
        self,
        backends: Sequence[Backend],
        governor: ComputationalGovernor | None = None,
        *,
        max_models: int = 5,
    ) -> None:
        self._all = list(backends)
        self.governor = governor
        self.max_models = max(1, max_models)
        self._fitted: list[Backend] = []
        self._dead: set[str] = set()

    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Fit every available backend. Failures are recorded, not raised."""
        self._fitted = []
        self._dead = set()
        for be in self._all:
            if not be.is_available():
                self._dead.add(be.name)
                continue
            try:
                be.fit(x, y)
                self._fitted.append(be)
            except Exception:
                self._dead.add(be.name)

    def active_backends(self) -> list[Backend]:
        """Backends that are fitted and allowed by the governor."""
        candidates = [b for b in self._fitted if b.name not in self._dead]
        if self.governor is None:
            return candidates[: self.max_models]

        # Refresh governor observation
        self.governor.observe()
        if self.governor.should_hold_only():
            return []

        allowed_names = set(
            self.governor.allowed_models([b.name for b in candidates])
        )
        active = [b for b in candidates if b.name in allowed_names]
        return active[: self.max_models]

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[list[list[float]]]:
        """Return list of proba matrices, one per active backend.

        Shape conceptually: (n_active, n_samples, n_classes).
        Returns empty list on SAFE_EXIT or no active models.
        """
        active = self.active_backends()
        if not active:
            return []
        out: list[list[list[float]]] = []
        for be in active:
            try:
                proba = be.predict_proba(x)
                out.append(proba)
            except Exception:
                self._dead.add(be.name)
        return out

    def names(self) -> list[str]:
        return [b.name for b in self.active_backends()]

    def status(self) -> dict[str, Any]:
        return {
            "fitted": [b.name for b in self._fitted],
            "dead": sorted(self._dead),
            "active": self.names(),
            "max_models": self.max_models,
            "governor_level": (
                self.governor.current_level().name
                if self.governor is not None
                else None
            ),
        }
