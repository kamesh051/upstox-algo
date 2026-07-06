"""CandleBuilder + IndicatorStream tests (pure, no network)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from trading_system.auth.token_store import IST
from trading_system.config.settings import FeedConfig
from trading_system.data.candles import (
    Candle,
    CandleBuilder,
    IndicatorStream,
    bucket_start,
)
from trading_system.data.indicators import add_indicators
from trading_system.data.live_feed import LiveFeed, Tick
from tests.conftest import make_ohlc
from tests.test_live_feed import FakeStreamer


def tick(sym: str, hh: int, mm: int, ss: int, ltp: float, ltq: int = 10) -> Tick:
    ts = datetime(2026, 7, 7, hh, mm, ss, tzinfo=IST)
    return Tick(f"KEY|{sym}", sym, ltp, ltq, ts, ts)


# -- bucketing -----------------------------------------------------------------


def test_bucket_start_alignment():
    ts = datetime(2026, 7, 7, 9, 17, 30, tzinfo=IST)
    assert bucket_start(ts, "1minute") == datetime(2026, 7, 7, 9, 17, tzinfo=IST)
    assert bucket_start(ts, "5minute") == datetime(2026, 7, 7, 9, 15, tzinfo=IST)
    ts2 = datetime(2026, 7, 7, 9, 22, 0, tzinfo=IST)
    assert bucket_start(ts2, "15minute") == datetime(2026, 7, 7, 9, 15, tzinfo=IST)


def test_unsupported_interval_rejected():
    with pytest.raises(ValueError):
        CandleBuilder(["60minute"])


# -- aggregation ------------------------------------------------------------------


def test_ohlcv_aggregation_and_emission_on_roll():
    b = CandleBuilder(["1minute"])
    assert b.on_tick(tick("RELIANCE", 9, 15, 5, 100.0)) == []
    assert b.on_tick(tick("RELIANCE", 9, 15, 30, 102.0)) == []
    assert b.on_tick(tick("RELIANCE", 9, 15, 45, 99.0)) == []
    assert b.on_tick(tick("RELIANCE", 9, 15, 59, 101.0)) == []
    out = b.on_tick(tick("RELIANCE", 9, 16, 1, 105.0))  # next bucket -> completes
    assert len(out) == 1
    c = out[0]
    assert c.ts == datetime(2026, 7, 7, 9, 15, tzinfo=IST)
    assert (c.open, c.high, c.low, c.close) == (100.0, 102.0, 99.0, 101.0)
    assert c.volume == 40
    assert c.interval == "1minute"


def test_quiet_gap_produces_no_phantom_candles():
    b = CandleBuilder(["1minute"])
    b.on_tick(tick("TCS", 9, 15, 10, 50.0))
    out = b.on_tick(tick("TCS", 9, 20, 0, 51.0))  # 4 empty minutes in between
    assert len(out) == 1  # only the 09:15 candle; no bars for empty minutes
    assert out[0].ts == datetime(2026, 7, 7, 9, 15, tzinfo=IST)


def test_late_tick_dropped_without_corruption():
    b = CandleBuilder(["1minute"])
    b.on_tick(tick("TCS", 9, 16, 5, 50.0))
    assert b.on_tick(tick("TCS", 9, 15, 59, 999.0)) == []  # late — dropped
    forming = b.flush()
    assert len(forming) == 1
    assert forming[0].high == 50.0  # late tick did not touch the forming candle


def test_multi_interval_simultaneous():
    b = CandleBuilder(["1minute", "5minute"])
    for mm, price in [(15, 100.0), (16, 101.0), (17, 102.0), (18, 103.0), (19, 104.0)]:
        b.on_tick(tick("INFY", 9, mm, 0, price))
    out = b.on_tick(tick("INFY", 9, 20, 0, 105.0))  # crosses both boundaries
    by_interval = {c.interval: c for c in out}
    assert set(by_interval) == {"1minute", "5minute"}
    five = by_interval["5minute"]
    assert five.ts == datetime(2026, 7, 7, 9, 15, tzinfo=IST)
    assert (five.open, five.high, five.low, five.close) == (100.0, 104.0, 100.0, 104.0)
    assert five.volume == 50


def test_symbols_are_independent():
    b = CandleBuilder(["1minute"])
    b.on_tick(tick("RELIANCE", 9, 15, 10, 100.0))
    b.on_tick(tick("TCS", 9, 15, 20, 50.0))
    out = b.on_tick(tick("RELIANCE", 9, 16, 0, 101.0))
    assert [c.symbol for c in out] == ["RELIANCE"]  # TCS candle still forming
    assert {c.symbol for c in b.flush()} == {"RELIANCE", "TCS"}


def test_flush_clears_forming():
    b = CandleBuilder(["1minute"])
    b.on_tick(tick("TCS", 9, 15, 10, 50.0))
    assert len(b.flush()) == 1
    assert b.flush() == []


# -- indicator stream ----------------------------------------------------------------


def frame_to_candles(df: pd.DataFrame, symbol: str = "SYM") -> list[Candle]:
    return [
        Candle(symbol, "KEY", "1minute", ts, r["open"], r["high"], r["low"], r["close"], int(r["volume"]))
        for ts, r in df.iterrows()
    ]


def test_indicator_stream_matches_batch_computation():
    raw = make_ohlc(days=10, seed=17)  # 250 rows
    stream = IndicatorStream()
    for candle in frame_to_candles(raw):
        last_row = stream.append(candle)
    batch = add_indicators(raw[["open", "high", "low", "close", "volume"]]).iloc[-1]
    for col in ("ema20", "ema200", "rsi14", "atr14", "vwap", "supertrend", "st_dir"):
        assert np.isclose(last_row[col], batch[col], equal_nan=True), col


def test_indicator_stream_window_trims():
    raw = make_ohlc(days=3, seed=9)  # 75 rows
    stream = IndicatorStream(window=50)
    for candle in frame_to_candles(raw):
        stream.append(candle)
    assert len(stream) == 50


def test_seed_with_sqlite_style_fixed_offset_tz():
    # CandleStore.load returns a FixedOffset(+05:30) index; live candles carry
    # ZoneInfo("Asia/Kolkata"). Mixing them must not degrade the index.
    raw = make_ohlc(days=2, seed=4)
    seed = raw.copy()
    seed.index = pd.to_datetime([ts.isoformat() for ts in raw.index])  # FixedOffset
    stream = IndicatorStream(seed=seed)
    live = frame_to_candles(raw)  # ZoneInfo timestamps
    last_candle = Candle(
        "SYM", "KEY", "1minute",
        raw.index[-1] + pd.Timedelta(minutes=15), 100.0, 101.0, 99.0, 100.5, 1000,
    )
    row = stream.append(last_candle)
    assert isinstance(stream._raw.index, pd.DatetimeIndex)
    assert not np.isnan(row["vwap"])  # daily VWAP reset needs .date on the index
    assert len(stream) == 51


def test_indicator_stream_seeding():
    raw = make_ohlc(days=4, seed=3)  # 100 rows
    seed, live = raw.iloc[:80], raw.iloc[80:]
    stream = IndicatorStream(seed=seed)
    assert len(stream) == 80
    for candle in frame_to_candles(live):
        row = stream.append(candle)
    assert len(stream) == 100
    assert not np.isnan(row["rsi14"])


# -- full pipeline replay: FakeStreamer -> LiveFeed -> builder -> indicators ---------


@pytest.mark.asyncio
async def test_pipeline_replay_end_to_end():
    key = "NSE_EQ|INE002A01018"
    base_ms = int(datetime(2026, 7, 7, 9, 15, 0, tzinfo=IST).timestamp() * 1000)

    def message(offset_sec: int, ltp: float):
        return {
            "feeds": {key: {"ltpc": {"ltp": ltp, "ltt": str(base_ms + offset_sec * 1000), "ltq": "10"}}}
        }

    def factory():
        f = FakeStreamer()

        def script(s):
            s.fire("open")
            for i in range(180):  # 3 minutes of ticks, one per second
                s.fire("message", message(i, 2900.0 + (i % 7)))

        f.on_connect = script
        return f

    feed = LiveFeed(
        access_token="tok",
        key_to_symbol={key: "RELIANCE"},
        cfg=FeedConfig(),
        streamer_factory=factory,
        clock=lambda: datetime(2026, 7, 7, 9, 18, tzinfo=IST),
    )
    builder = CandleBuilder(["1minute"])
    stream = IndicatorStream()
    candles: list[Candle] = []

    async def consume():
        async for t in feed.ticks():
            for c in builder.on_tick(t):
                stream.append(c)
                candles.append(c)
            if len(candles) >= 2:
                return

    task = asyncio.create_task(feed.run())
    await asyncio.wait_for(consume(), timeout=3)
    feed.stop()
    await asyncio.wait_for(task, timeout=2)

    assert [c.ts.minute for c in candles] == [15, 16]
    assert all(c.volume == 600 for c in candles)  # 60 ticks x qty 10
    assert len(stream) == 2
