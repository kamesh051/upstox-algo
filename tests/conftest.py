"""Shared fixtures: deterministic synthetic intraday OHLCV data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

IST = ZoneInfo("Asia/Kolkata")
CANDLES_PER_DAY = 25  # 15m candles, 09:15 .. 15:15 start times


def make_ohlc(
    days: int = 40,
    start_day: date = date(2026, 1, 5),
    start_price: float = 1000.0,
    seed: int = 7,
    vol: float = 0.002,
) -> pd.DataFrame:
    """Seeded random-walk 15-minute OHLCV series over NSE session times."""
    rng = np.random.default_rng(seed)
    rows = []
    price = start_price
    day = start_day
    produced = 0
    while produced < days:
        if day.weekday() < 5:  # trading days only
            for k in range(CANDLES_PER_DAY):
                ts = datetime.combine(day, datetime.min.time(), tzinfo=IST).replace(
                    hour=9, minute=15
                ) + timedelta(minutes=15 * k)
                o = price
                ret = rng.normal(0, vol)
                c = o * (1 + ret)
                hi = max(o, c) * (1 + abs(rng.normal(0, vol / 2)))
                lo = min(o, c) * (1 - abs(rng.normal(0, vol / 2)))
                rows.append(
                    {
                        "timestamp": ts,
                        "open": o,
                        "high": hi,
                        "low": lo,
                        "close": c,
                        "volume": int(rng.integers(1_000, 50_000)),
                        "oi": 0,
                    }
                )
                price = c
            produced += 1
        day += timedelta(days=1)
    return pd.DataFrame(rows).set_index("timestamp")


@pytest.fixture
def ohlc_40d() -> pd.DataFrame:
    return make_ohlc(days=40)
