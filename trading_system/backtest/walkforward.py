"""Walk-forward date-range splitting.

Produces rolling (train, validate) windows over a date range. Parameter
optimization on these windows is deliberately out of scope for now — this is
the infrastructure it will run on later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class WalkForwardSplit:
    train_from: date
    train_to: date
    validate_from: date
    validate_to: date


def walk_forward_splits(
    from_date: date,
    to_date: date,
    train_days: int,
    validate_days: int,
) -> list[WalkForwardSplit]:
    """Non-overlapping validate windows, each preceded by its train window.

    The window steps forward by ``validate_days`` so every validate range is
    out-of-sample exactly once. Trailing days that cannot fill a complete
    validate window are dropped.
    """
    if train_days < 1 or validate_days < 1:
        raise ValueError("train_days and validate_days must be >= 1")
    if from_date > to_date:
        raise ValueError("from_date after to_date")

    splits: list[WalkForwardSplit] = []
    train_start = from_date
    while True:
        train_end = train_start + timedelta(days=train_days - 1)
        val_start = train_end + timedelta(days=1)
        val_end = val_start + timedelta(days=validate_days - 1)
        if val_end > to_date:
            break
        splits.append(WalkForwardSplit(train_start, train_end, val_start, val_end))
        train_start = train_start + timedelta(days=validate_days)
    return splits
