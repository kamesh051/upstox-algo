"""build_live_seed + fetch_intraday + CandleBuilder partial-drop.

Covers the 2026-07-03 incident: stale cache + mid-session start produced a
spurious Supertrend flip and a bogus paper short.
"""

from datetime import date, datetime

import httpx
import respx

from trading_system.auth.token_store import IST
from trading_system.data.candles import CandleBuilder
from trading_system.data.historical import (
    CandleStore,
    HistoricalDownloader,
    build_live_seed,
)
from trading_system.ratelimit import TokenBucket
from tests.test_candles import tick

KEY = "NSE_EQ|TEST"
NOW = datetime(2026, 7, 3, 11, 7, 0, tzinfo=IST)  # mid-session, mid-bucket


def make_downloader(tmp_path):
    store = CandleStore(tmp_path / "seed.sqlite")
    dl = HistoricalDownloader(
        store=store,
        token_provider=lambda: "tok",
        rate_limiter=TokenBucket(rate_per_sec=10_000, burst=100),
    )
    return store, dl


def candle_row(ts: str, px: float = 100.0, vol: int = 1000):
    return [ts, px, px + 1, px - 1, px + 0.5, vol, 0]


HIST_JSON = {
    "status": "success",
    "data": {"candles": [candle_row("2026-07-02T09:15:00+05:30")]},
}
INTRADAY_JSON = {
    "status": "success",
    "data": {
        "candles": [
            candle_row("2026-07-03T11:00:00+05:30", 103),  # forming bucket at NOW
            candle_row("2026-07-03T10:45:00+05:30", 102),
            candle_row("2026-07-03T09:15:00+05:30", 101),
        ]
    },
}


@respx.mock
def test_seed_backfills_and_appends_intraday(tmp_path):
    store, dl = make_downloader(tmp_path)
    # cache holds only Jul 1 -> Jul 2 must be backfilled, Jul 3 comes intraday
    store.upsert(KEY, "15minute", [candle_row("2026-07-01T15:15:00+05:30")])

    hist = respx.get(url__regex=r".*/v3/historical-candle/NSE_EQ\|TEST/minutes/15/.*").mock(
        return_value=httpx.Response(200, json=HIST_JSON)
    )
    intraday = respx.get(
        url__regex=r".*/v3/historical-candle/intraday/NSE_EQ\|TEST/minutes/15"
    ).mock(return_value=httpx.Response(200, json=INTRADAY_JSON))

    seed = build_live_seed(store, dl, KEY, "15minute", now=NOW)

    assert hist.called and intraday.called
    days = [d.isoformat() for d in seed.index.date]
    assert "2026-07-01" in days and "2026-07-02" in days and "2026-07-03" in days
    # forming 11:00 bucket excluded (NOW=11:07); completed 09:15/10:45 included
    times_today = [ts.time().isoformat() for ts in seed.index if ts.date() == date(2026, 7, 3)]
    assert "09:15:00" in times_today and "10:45:00" in times_today
    assert "11:00:00" not in times_today
    assert seed.index.is_monotonic_increasing
    # Jul 2 backfill landed in the cache too (persistent, not just this seed)
    assert store.latest_ts(KEY, "15minute").date() == date(2026, 7, 2)


@respx.mock
def test_seed_survives_intraday_failure(tmp_path):
    store, dl = make_downloader(tmp_path)
    store.upsert(KEY, "15minute", [candle_row("2026-07-02T15:15:00+05:30")])
    respx.get(url__regex=r".*/intraday/.*").mock(return_value=httpx.Response(500))
    seed = build_live_seed(store, dl, KEY, "15minute", now=NOW)
    assert len(seed) == 1  # cache-only fallback, no exception


@respx.mock
def test_seed_empty_cache_backfills_window(tmp_path):
    store, dl = make_downloader(tmp_path)
    hist = respx.get(url__regex=r".*/minutes/15/.*").mock(
        return_value=httpx.Response(200, json=HIST_JSON)
    )
    respx.get(url__regex=r".*/intraday/.*").mock(
        return_value=httpx.Response(200, json=INTRADAY_JSON)
    )
    seed = build_live_seed(store, dl, KEY, "15minute", now=NOW, backfill_days_if_empty=90)
    assert hist.called
    assert not seed.empty


def test_latest_ts(tmp_path):
    store = CandleStore(tmp_path / "x.sqlite")
    assert store.latest_ts(KEY, "15minute") is None
    store.upsert(KEY, "15minute", [candle_row("2026-07-01T15:15:00+05:30")])
    assert store.latest_ts(KEY, "15minute") == datetime.fromisoformat(
        "2026-07-01T15:15:00+05:30"
    )


# -- partial first candle -----------------------------------------------------------


def test_partial_first_candle_dropped():
    b = CandleBuilder(["15minute"], drop_partial_first=True)
    # session starts 11:07 — mid-bucket ticks for the 11:00 candle
    b.on_tick(tick("RELIANCE", 11, 7, 0, 100.0))
    b.on_tick(tick("RELIANCE", 11, 12, 0, 101.0))
    out = b.on_tick(tick("RELIANCE", 11, 15, 1, 102.0))  # roll: 11:00 completes
    assert out == []  # partial candle swallowed, not emitted
    out = b.on_tick(tick("RELIANCE", 11, 30, 1, 103.0))  # 11:15 candle completes
    assert len(out) == 1 and out[0].ts.minute == 15  # full candle flows normally


def test_on_time_first_candle_kept():
    b = CandleBuilder(["15minute"], drop_partial_first=True)
    b.on_tick(tick("RELIANCE", 9, 15, 2, 100.0))  # 2s into the bucket: fine
    out = b.on_tick(tick("RELIANCE", 9, 30, 1, 101.0))
    assert len(out) == 1 and out[0].ts.minute == 15


def test_flush_drops_partial():
    b = CandleBuilder(["15minute"], drop_partial_first=True)
    b.on_tick(tick("RELIANCE", 11, 7, 0, 100.0))
    assert b.flush() == []


def test_partial_drop_disabled_by_default():
    b = CandleBuilder(["15minute"])
    b.on_tick(tick("RELIANCE", 11, 7, 0, 100.0))
    out = b.on_tick(tick("RELIANCE", 11, 15, 1, 102.0))
    assert len(out) == 1  # old behavior preserved


def test_partial_tracking_is_per_symbol():
    b = CandleBuilder(["15minute"], drop_partial_first=True)
    b.on_tick(tick("RELIANCE", 11, 7, 0, 100.0))   # partial start
    b.on_tick(tick("TCS", 11, 15, 1, 50.0))        # on-time start for its bucket
    out = b.on_tick(tick("RELIANCE", 11, 15, 2, 101.0))
    assert out == []  # RELIANCE 11:00 dropped
    out = b.on_tick(tick("TCS", 11, 30, 1, 51.0))
    assert len(out) == 1 and out[0].symbol == "TCS"  # TCS unaffected