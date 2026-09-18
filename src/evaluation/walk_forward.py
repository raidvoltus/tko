"""Point-in-time walk-forward splits with purge gap. No random shuffle."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class WalkForwardSplit:
    fold: int
    train_start: int
    train_end: int
    purge_end: int
    valid_end: int
    test_end: int

    def validate(self) -> None:
        assert self.train_start < self.train_end <= self.purge_end <= self.valid_end <= self.test_end


def generate_walk_forward_splits(
    n_samples: int,
    train_size: int,
    valid_size: int,
    test_size: int,
    purge_size: int,
    step: Optional[int] = None,
) -> List[WalkForwardSplit]:
    step = step or test_size
    folds: List[WalkForwardSplit] = []
    start = 0
    fold = 0
    while True:
        train_end = start + train_size
        purge_end = train_end + purge_size
        valid_end = purge_end + valid_size
        test_end = valid_end + test_size
        if test_end > n_samples:
            break
        sp = WalkForwardSplit(fold, start, train_end, purge_end, valid_end, test_end)
        sp.validate()
        folds.append(sp)
        fold += 1
        start += step
    return folds
