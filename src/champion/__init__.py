"""Champion–Challenger framework. No order authority."""
from src.champion.manifest import ChampionManifest, compute_config_hash
from src.champion.promotion import PromotionGate
from src.champion.registry import ChampionRegistry
from src.champion.states import ChallengerState, PromotionDecision

__all__ = [
    "ChampionManifest",
    "compute_config_hash",
    "ChampionRegistry",
    "ChallengerState",
    "PromotionDecision",
    "PromotionGate",
]
