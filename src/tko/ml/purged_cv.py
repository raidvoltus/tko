"""Purged K-Fold, embargo, and combinatorial purged CV (CPCV)."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Iterator, Sequence


@dataclass(frozen=True, slots=True)
class Fold:
    train_idx: tuple[int, ...]
    test_idx: tuple[int, ...]


@dataclass
class CpcvResult:
    folds: list[Fold] = field(default_factory=list)
    n_groups: int = 0
    n_test_groups: int = 0


def purged_kfold(
    n: int,
    *,
    n_splits: int = 5,
    embargo: int = 0,
    label_horizon: int = 0,
) -> list[Fold]:
    """Time-ordered purged K-Fold with embargo around test.

    Purge removes train indices whose label horizon overlaps test window.
    """
    if n_splits < 2 or n < n_splits * 2:
        return []
    fold_sizes = [n // n_splits] * n_splits
    for i in range(n % n_splits):
        fold_sizes[i] += 1
    bounds: list[tuple[int, int]] = []
    start = 0
    for sz in fold_sizes:
        bounds.append((start, start + sz))
        start += sz
    folds: list[Fold] = []
    for ti, (ts, te) in enumerate(bounds):
        test = list(range(ts, te))
        train: list[int] = []
        for tr_i, (trs, tre) in enumerate(bounds):
            if tr_i == ti:
                continue
            for i in range(trs, tre):
                # purge: drop train samples whose label window intersects test
                label_end = i + label_horizon
                if label_horizon > 0 and not (label_end < ts or i > te):
                    continue
                # embargo around test
                if embargo > 0 and abs(i - ts) <= embargo:
                    continue
                if embargo > 0 and abs(i - (te - 1)) <= embargo:
                    continue
                train.append(i)
        if train and test:
            folds.append(Fold(train_idx=tuple(train), test_idx=tuple(test)))
    return folds


def combinatorial_purged_cv(
    n: int,
    *,
    n_groups: int = 6,
    n_test_groups: int = 2,
    embargo: int = 0,
    label_horizon: int = 0,
) -> CpcvResult:
    """CPCV: all combinations of test groups among ordered groups."""
    if n_groups < 2 or n_test_groups < 1 or n_test_groups >= n_groups or n < n_groups:
        return CpcvResult()
    group_size = n // n_groups
    groups: list[list[int]] = []
    for g in range(n_groups):
        s = g * group_size
        e = n if g == n_groups - 1 else (g + 1) * group_size
        groups.append(list(range(s, e)))
    folds: list[Fold] = []
    for test_gs in combinations(range(n_groups), n_test_groups):
        test_set = set()
        for g in test_gs:
            test_set.update(groups[g])
        test_idx = sorted(test_set)
        train_idx: list[int] = []
        for g in range(n_groups):
            if g in test_gs:
                continue
            for i in groups[g]:
                label_end = i + label_horizon
                # purge vs any test index range
                if label_horizon > 0 and test_idx:
                    if not (label_end < test_idx[0] or i > test_idx[-1]):
                        continue
                if embargo > 0 and test_idx:
                    if any(abs(i - t) <= embargo for t in (test_idx[0], test_idx[-1])):
                        continue
                train_idx.append(i)
        if train_idx and test_idx:
            folds.append(Fold(train_idx=tuple(train_idx), test_idx=tuple(test_idx)))
    return CpcvResult(folds=folds, n_groups=n_groups, n_test_groups=n_test_groups)
