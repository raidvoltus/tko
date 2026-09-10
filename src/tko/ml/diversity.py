"""Diversity helpers — measure and select complementary models.

Pure functions. Do not mutate model state or trading logic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# Family grouping to avoid selecting near-duplicates from same family
FAMILY: dict[str, str] = {
    "logreg": "linear",
    "ridge": "linear",
    "lightgbm": "boosting",
    "histgbm": "boosting",
    "extratrees": "bagging",
    "randomforest": "bagging",
    "gaussiannb": "probabilistic",
    "knn": "instance",
}


def prediction_correlation(
    preds_a: Sequence[float],
    preds_b: Sequence[float],
) -> float:
    """Pearson correlation between two prediction sequences.

    Returns 0.0 on empty/constant inputs or any numeric error.
    """
    n = min(len(preds_a), len(preds_b))
    if n < 2:
        return 0.0
    a = [float(preds_a[i]) for i in range(n)]
    b = [float(preds_b[i]) for i in range(n)]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    num = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    den_a = sum((a[i] - mean_a) ** 2 for i in range(n)) ** 0.5
    den_b = sum((b[i] - mean_b) ** 2 for i in range(n)) ** 0.5
    den = den_a * den_b
    if den <= 0.0:
        return 0.0
    corr = num / den
    # Clamp numeric noise
    return max(-1.0, min(1.0, corr))


def diversity_matrix(
    preds_dict: Mapping[str, Sequence[float]],
) -> dict[tuple[str, str], float]:
    """Pairwise correlation for every ordered pair of models."""
    names = list(preds_dict.keys())
    out: dict[tuple[str, str], float] = {}
    for i, na in enumerate(names):
        for nb in names[i + 1 :]:
            c = prediction_correlation(preds_dict[na], preds_dict[nb])
            out[(na, nb)] = c
            out[(nb, na)] = c
    return out


def select_diverse(
    preds_dict: Mapping[str, Sequence[float]],
    threshold: float = 0.80,
    max_per_family: int = 1,
) -> list[str]:
    """Greedy select models with pairwise correlation < threshold.

    Keeps at most max_per_family models from the same FAMILY.
    Order of insertion follows the order of keys in preds_dict.
    """
    selected: list[str] = []
    family_count: dict[str, int] = {}

    for name in preds_dict:
        fam = FAMILY.get(name, name)
        if family_count.get(fam, 0) >= max_per_family:
            continue
        # Check correlation against already selected
        redundant = False
        for prev in selected:
            c = prediction_correlation(preds_dict[name], preds_dict[prev])
            # Only drop high *positive* correlation (same direction).
            # Strong negative correlation is complementary, keep both.
            if c >= threshold:
                redundant = True
                break
        if redundant:
            continue
        selected.append(name)
        family_count[fam] = family_count.get(fam, 0) + 1
    return selected
