"""Backend registry for Stage 5.1 model pool.

All backends are optional. Missing libraries → is_available()=False.
"""

from __future__ import annotations

from tko.ml.backends.base import Backend
from tko.ml.backends.extratrees_backend import ExtraTreesBackend
from tko.ml.backends.gaussiannb_backend import GaussianNBBackend
from tko.ml.backends.histgbm_backend import HistGBMBackend
from tko.ml.backends.lightgbm_backend import LightGBMBackend
from tko.ml.backends.logreg_backend import LogRegBackend

BACKENDS: dict[str, type[Backend]] = {
    "lightgbm": LightGBMBackend,
    "logreg": LogRegBackend,
    "extratrees": ExtraTreesBackend,
    "histgbm": HistGBMBackend,
    "gaussiannb": GaussianNBBackend,
}

__all__ = [
    "BACKENDS",
    "Backend",
    "LightGBMBackend",
    "LogRegBackend",
    "ExtraTreesBackend",
    "HistGBMBackend",
    "GaussianNBBackend",
]
