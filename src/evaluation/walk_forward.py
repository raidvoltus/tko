"""Point-in-time walk-forward splits with purge gap. No random shuffle."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class WalkForwardSplit:
    """
    Index windows (half-open [start, end)).

    Layout per fold:
      [train_start, train_end) train
      [train_end, purge_end) purge/embargo
      [purge_end, cal_fit_end) calibration fit
      [cal_fit_end, cal_select_end) calibration selection
      [cal_select_end, test_end) untouched OOS/test
    """

    fold: int
    train_start: int
    train_end: int
    purge_end: int
    cal_fit_end: int
    cal_select_end: int
    test_end: int
    purge_size: int = 0
    embargo_size: int = 0

    # backward-compat aliases used by older runner code
    @property
    def valid_end(self) -> int:
        """End of selection segment (legacy name)."""
        return self.cal_select_end

    def validate(self) -> None:
        assert (
            self.train_start
            < self.train_end
            <= self.purge_end
            <= self.cal_fit_end
            <= self.cal_select_end
            <= self.test_end
        )

    def to_meta(self) -> Dict[str, Any]:
        d = asdict(self)
        d["valid_end"] = self.valid_end
        return d


def generate_walk_forward_splits(
    n_samples: int,
    train_size: int,
    valid_size: int,
    test_size: int,
    purge_size: int,
    step: Optional[int] = None,
    cal_fit_frac: float = 0.5,
) -> List[WalkForwardSplit]:
    """
    valid_size is split into cal_fit + cal_select (default 50/50).
    test_size is untouched OOS.
    """
    step = step or test_size
    folds: List[WalkForwardSplit] = []
    start = 0
    fold = 0
    cal_fit_frac = min(0.9, max(0.1, cal_fit_frac))
    while True:
        train_end = start + train_size
        purge_end = train_end + purge_size
        valid_end = purge_end + valid_size
        test_end = valid_end + test_size
        if test_end > n_samples:
            break
        cal_fit_len = max(1, int(valid_size * cal_fit_frac))
        cal_fit_end = purge_end + cal_fit_len
        cal_select_end = valid_end
        sp = WalkForwardSplit(
            fold=fold,
            train_start=start,
            train_end=train_end,
            purge_end=purge_end,
            cal_fit_end=cal_fit_end,
            cal_select_end=cal_select_end,
            test_end=test_end,
            purge_size=purge_size,
            embargo_size=purge_size,
        )
        sp.validate()
        folds.append(sp)
        fold += 1
        start += step
    return folds
