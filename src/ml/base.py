"""MLModel interface - train/predict/save/load with integrity checks."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ModelMetadata:
    name: str
    version: str
    framework: str
    feature_schema: List[str]
    created_at: str
    train_samples: int = 0
    metrics: Dict[str, float] = field(default_factory=dict)
    seed: Optional[int] = None
    checksum: str = ""
    python_version: str = "3.8"
    notes: str = ""


class MLModel(ABC):
    """Abstract interface. ML never has direct order authority."""

    def __init__(self, name: str = "baseline", version: str = "1.0.0"):
        self.name = name
        self.version = version
        self.feature_schema: List[str] = []
        self.metadata: Optional[ModelMetadata] = None
        self._model = None
        self.is_loaded = False

    @abstractmethod
    def train(self, X: np.ndarray, y: np.ndarray, feature_names: List[str], **kwargs) -> Dict[str, float]:
        ...

    @abstractmethod
    def predict(self, X: np.ndarray) -> np.ndarray:
        ...

    @abstractmethod
    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        ...

    def save(self, path: str) -> str:
        """Save model + metadata + checksum. Returns path to package dir."""
        os.makedirs(path, exist_ok=True)
        model_path = os.path.join(path, "model.pkl")
        meta_path = os.path.join(path, "metadata.json")

        with open(model_path, "wb") as f:
            pickle.dump(self._model, f, protocol=4)  # protocol 4 = py3.8 compatible

        checksum = self._file_sha256(model_path)
        if self.metadata is None:
            self.metadata = ModelMetadata(
                name=self.name,
                version=self.version,
                framework=self.__class__.__name__,
                feature_schema=self.feature_schema,
                created_at=datetime.utcnow().isoformat() + "Z",
                checksum=checksum,
            )
        else:
            self.metadata.checksum = checksum
            self.metadata.feature_schema = self.feature_schema

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self.metadata), f, indent=2)

        # also write checksum file
        with open(os.path.join(path, "checksum.sha256"), "w") as f:
            f.write(checksum + "\n")

        logger.info("Model saved to %s (checksum=%s)", path, checksum[:12])
        return path

    def load(self, path: str) -> bool:
        """Load and verify integrity. Returns False on failure (does not crash)."""
        model_path = os.path.join(path, "model.pkl")
        meta_path = os.path.join(path, "metadata.json")
        if not os.path.isfile(model_path) or not os.path.isfile(meta_path):
            logger.error("Model files missing at %s", path)
            self.is_loaded = False
            return False
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_dict = json.load(f)
            self.metadata = ModelMetadata(**meta_dict)
            expected = self.metadata.checksum
            actual = self._file_sha256(model_path)
            if expected and actual != expected:
                logger.error("Model checksum mismatch: expected %s got %s", expected[:12], actual[:12])
                self.is_loaded = False
                return False
            with open(model_path, "rb") as f:
                self._model = pickle.load(f)
            self.feature_schema = self.metadata.feature_schema
            self.name = self.metadata.name
            self.version = self.metadata.version
            self.is_loaded = True
            logger.info("Model loaded: %s v%s", self.name, self.version)
            return True
        except Exception as e:
            logger.exception("Failed to load model from %s: %s", path, e)
            self.is_loaded = False
            return False

    def validate_features(self, feature_names: List[str]) -> bool:
        if not self.feature_schema:
            return True
        if list(feature_names) != list(self.feature_schema):
            logger.error(
                "Feature schema mismatch. Expected %s got %s",
                self.feature_schema,
                feature_names,
            )
            return False
        return True

    @staticmethod
    def _file_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
