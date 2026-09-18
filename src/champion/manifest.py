"""Immutable champion / challenger identity manifests."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


def compute_config_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


@dataclass(frozen=True)
class ChampionManifest:
    champion_id: str
    feature_version: str
    feature_schema_hash: str
    strategy_version: str
    strategy_config_hash: str
    regime_version: str
    ml_model_id: str
    ml_model_hash: str
    ensemble_version: str
    governor_version: str
    threshold_config_hash: str
    risk_policy_version: str
    code_commit: str
    created_at: float
    effective_from: float
    training_data_cutoff: Optional[str] = None
    validation_window: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def identity_hash(self) -> str:
        """Stable production identity — excludes wall-clock timestamps.

        created_at / effective_from are metadata for audit, not identity.
        Tamper-evident storage (signature) is out of band; identity is reproducible
        from schema/config/model/commit hashes alone.
        """
        d = self.to_dict()
        for k in ("created_at", "effective_from"):
            d.pop(k, None)
        return compute_config_hash(d)

    @staticmethod
    def current_production(
        code_commit: str = "unknown",
        feature_schema_hash: str = "",
        strategy_config_hash: str = "",
        threshold_config_hash: str = "",
        ml_model_hash: str = "none",
    ) -> "ChampionManifest":
        now = time.time()
        return ChampionManifest(
            champion_id="tko-champion-v1",
            feature_version="v2",
            feature_schema_hash=feature_schema_hash or "pending",
            strategy_version="v1-confluence",
            strategy_config_hash=strategy_config_hash or "pending",
            regime_version="v1-hysteresis",
            ml_model_id="none",
            ml_model_hash=ml_model_hash,
            ensemble_version="v1-weighted",
            governor_version="v1-policy",
            threshold_config_hash=threshold_config_hash or "pending",
            risk_policy_version="v1-risk-engine",
            code_commit=code_commit,
            created_at=now,
            effective_from=now,
            training_data_cutoff=None,
            validation_window=None,
            notes="Production champion — risk remains sole order authority",
        )


@dataclass
class ChallengerManifest:
    challenger_id: str
    challenger_type: str  # FEATURE|STRATEGY|REGIME|EDGE|ML|ENSEMBLE|GOVERNOR
    parent_champion_id: str
    feature_version: str
    strategy_version: str
    regime_version: str
    config_hash: str
    code_commit: str
    created_at: float = field(default_factory=time.time)
    state: str = "CANDIDATE"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
