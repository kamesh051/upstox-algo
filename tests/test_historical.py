from datetime import date

import httpx
import pytest
import respx

from trading_system.auth.token_store import AuthError
from trading_system.data.historical import (
    CandleStore,
    HistoricalDownloader,
    chunk_date_range,
    parse_interval,
)
from trading_system.ratelimit import TokenBucket


# -- chunking ------------------------------------------------------------------


def test_chunk_single_window():
    chunks = chunk_date_range(date(2026, 1, 1), date(2026, 1, 20), max_days=28)
    assert chunks == [(date(2026, 1, 1), date(2026, 1, 20))]


def test_chunk_splits_year_into_month_windows():
    chunks = chunk_date_range(date(2025, 7, 1), date(2026, 6, 30), max_days=28)
    # Windows are contiguous, non-overlapping, and cover the full range
    assert chunks[0][0] == date(2025, 7, 1)
    assert chunks[-1][1] == date(2026, 6, 30)
    for (s1, e1), (s2, _) in zip(chunks, chunks[1:]):
        assert (s2 - e1).days == 1
        assert (e1 - s1).days <= 27
    assert len(chunks) == 14  # 365 days / 28 → 14 windows


def test_chunk_rejects_inverted_range():
    with pytest.raises(ValueError):
        chunk_date_range(date(2026, 2, 1), date(2026, 1, 1), max_days=28)


def test_parse_interval():
    spec = parse_interval("15minute")
    assert spec.url_part == "minutes/15"
    assert spec.max_days_per_request == 28
    with pytest.raises(ValueError):
        parse_interval("2hour")


# -- candle store ----------------------------------------------------------------

RAW_CANDLES = [
    # API returns newest-first
    ["2026-01-02T09:30:00+05:30", 101.0, 102.5, 100.5, 102.0, 5000, 0],
    ["2026-01-02T09:15:00+05:30", 100.0, 101.5, 99.5, 101.0, 8000, 0],
]


def test_store_upsert_and_load_sorted(tmp_path):
    store = CandleStore(tmp_path / "test.sqlite")
    n = store.upsert("NSE_EQ|INE002A01018", "15minute", RAW_CANDLES)
    assert n == 2
    df = store.load("NSE_EQ|INE002A01018", "15minute")
    assert len(df) == 2
    assert df.index.is_monotonic_increasing  # oldest first despite API order
    assert df.iloc[0]["open"] == 100.0
    assert df.iloc[1]["close"] == 102.0
    store.close()


def test_store_upsert_is_idempotent(tmp_path):
    store = CandleStore(tmp_path / "test.sqlite")
    store.upsert("KEY", "15minute", RAW_CANDLES)
    store.upsert("KEY", "15minute", RAW_CANDLES)  # re-download same window
    assert store.count("KEY", "15minute") == 2
    store.close()


def test_store_date_filter(tmp_path):
    store = CandleStore(tmp_path / "test.sqlite")
    store.upsert("KEY", "15minute", RAW_CANDLES)
    df = store.load("KEY", "15minute", from_date=date(2026, 1, 2), to_date=date(2026, 1, 2))
    assert len(df) == 2
    df = store.load("KEY", "15minute", from_date=date(2026, 1, 3))
    assert df.empty
    store.close()


# -- downloader (mocked HTTP) ----------------------------------------------------


def _make_downloader(tmp_path, token="valid-token"):
    store = CandleStore(tmp_path / "dl.sqlite")
    return store, HistoricalDownloader(
        store=store,
        token_provider=lambda: token,
        api_base="https://api.upstox.com",
        rate_limiter=TokenBucket(rate_per_sec=10_000, burst=100),
    )


@respx.mock
def test_download_paginates_and_caches(tmp_path):
    route = respx.get(url__regex=r"https://api\.upstox\.com/v3/historical-candle/.*").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": {"candles": RAW_CANDLES}}
        )
    )
    store, dl = _make_downloader(tmp_path)
    total = dl.download("NSE_EQ|X", "15minute", date(2026, 1, 1), date(2026, 3, 1))
    # 60 days at 28-day windows → 3 requests
    assert route.call_count == 3
    assert total == 6
    # Same (key, interval, ts) rows collapse in the cache
    assert store.count("NSE_EQ|X", "15minute") == 2
    # Auth header carried on every request
    assert route.calls[0].request.headers["Authorization"] == "Bearer valid-token"
    store.close()


@respx.mock
def test_download_401_raises_auth_error(tmp_path):
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(401, json={}))
    store, dl = _make_downloader(tmp_path)
    with pytest.raises(AuthError, match="03:30 IST"):
        dl.download("NSE_EQ|X", "15minute", date(2026, 1, 1), date(2026, 1, 5))
    store.close()


@respx.mock
def test_download_error_status_raises(tmp_path):
    respx.get(url__regex=r".*").mock(
        return_value=httpx.Response(200, json={"status": "error", "errors": ["boom"]})
    )
    store, dl = _make_downloader(tmp_path)
    with pytest.raises(RuntimeError):
        dl.download("NSE_EQ|X", "15minute", date(2026, 1, 1), date(2026, 1, 5))
    store.close()
