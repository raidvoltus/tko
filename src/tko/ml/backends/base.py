"""Uniform backend interface for the Stage 5.1 model pool."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any


class Backend(ABC):
    """Minimal interface every model backend must implement."""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """True if the required library is importable and usable."""

    @abstractmethod
    def fit(self, x: Sequence[Sequence[float]], y: Sequence[int]) -> None:
        """Train on features X and integer labels y (0/1/2)."""

    @abstractmethod
    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[list[float]]:
        """Return per-class probabilities, shape (n_samples, n_classes)."""

    def get_params(self) -> dict[str, Any]:
        return {"name": self.name}
