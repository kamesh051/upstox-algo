"""Historical OHLC downloader with SQLite caching.

Uses the Upstox **v3** historical-candle endpoint::

    GET /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}

v3 (not v2) because v2 only serves 1-/30-minute intraday candles; 15-minute
requires v3's ``minutes/15``. The API returns at most ~1 month of minute data
per request, so date ranges are chunked and each chunk upserted into SQLite —
re-running a download is idempotent and only fetches what's asked, while reads
always come from the local cache.

Candle prices are stored as float (market data feeds indicator math); money
values elsewhere in the system are paise ints per project convention.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import httpx
import pandas as pd

from trading_system.auth.token_store import AuthError
from trading_system.logging_setup import get_logger
from trading_system.ratelimit import TokenBucket

log = get_logger(__name__)

CANDLE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]


# -- interval handling --------------------------------------------------------


@dataclass(frozen=True)
class IntervalSpec:
    unit: str  # v3 URL unit: minutes | hours | days
    value: int  # v3 URL interval
    max_days_per_request: int  # API window limit for this granularity

    @property
    def url_part(self) -> str:
        return f"{self.unit}/{self.value}"

    @property
    def name(self) -> str:
        return f"{self.value}{self.unit.rstrip('s')}"  # e.g. "15minute" — cache key


_INTERVALS: dict[str, IntervalSpec] = {
    # v3 limits: minutes 1-15 → 1 month/request, 16-300 → 1 quarter, days → 1 decade
    "1minute": IntervalSpec("minutes", 1, 28),
    "3minute": IntervalSpec("minutes", 3, 28),
    "5minute": IntervalSpec("minutes", 5, 28),
    "15minute": IntervalSpec("minutes", 15, 28),
    "30minute": IntervalSpec("minutes", 30, 84),
    "60minute": IntervalSpec("minutes", 60, 84),
    "day": IntervalSpec("days", 1, 3650),
}


def parse_interval(name: str) -> IntervalSpec:
    try:
        return _INTERVALS[name]
    except KeyError:
        valid = ", ".join(sorted(_INTERVALS))
        raise ValueError(f"Unknown interval {name!r}. Valid: {valid}") from None


def chunk_date_range(
    from_date: date, to_date: date, max_days: int
) -> list[tuple[date, date]]:
    """Split [from_date, to_date] (inclusive) into windows of at most max_days."""
    if from_date > to_date:
        raise ValueError(f"from_date {from_date} is after to_date {to_date}")
    chunks: list[tuple[date, date]] = []
    start = from_date
    while start <= to_date:
        end = min(start + timedelta(days=max_days - 1), to_date)
        chunks.append((start, end))
        start = end + timedelta(days=1)
    return chunks


# -- SQLite cache --------------------------------------------------------------


class CandleStore:
    """One table keyed by (instrument_key, interval, ts); upserts are idempotent."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                instrument_key TEXT NOT NULL,
                interval       TEXT NOT NULL,
                ts             TEXT NOT NULL,   -- ISO8601 with IST offset
                open   REAL NOT NULL,
                high   REAL NOT NULL,
                low    REAL NOT NULL,
                close  REAL NOT NULL,
                volume INTEGER NOT NULL,
                oi     INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (instrument_key, interval, ts)
            )
            """
        )
        self._conn.commit()

    def upsert(self, instrument_key: str, interval: str, candles: list[list]) -> int:
        """candles: raw API rows [ts, open, high, low, close, volume, oi]."""
        rows = [
            (instrument_key, interval, c[0], c[1], c[2], c[3], c[4], int(c[5]), int(c[6]))
            for c in candles
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)", rows
        )
        self._conn.commit()
        return len(rows)

    def load(
        self,
        instrument_key: str,
        interval: str,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> pd.DataFrame:
        """Cached candles as a DataFrame indexed by timestamp, oldest first."""
        query = "SELECT ts, open, high, low, close, volume, oi FROM candles WHERE instrument_key=? AND interval=?"
        params: list = [instrument_key, interval]
        if from_date:
            query += " AND ts >= ?"
            params.append(from_date.isoformat())
        if to_date:
            # ts is ISO datetime; day + 1 keeps the whole to_date inclusive
            query += " AND ts < ?"
            params.append((to_date + timedelta(days=1)).isoformat())
        query += " ORDER BY ts ASC"
        df = pd.read_sql_query(query, self._conn, params=params)
        if df.empty:
            return pd.DataFrame(columns=CANDLE_COLUMNS).set_index("timestamp")
        df["timestamp"] = pd.to_datetime(df.pop("ts"))
        return df.set_index("timestamp")

    def latest_ts(self, instrument_key: str, interval: str) -> datetime | None:
        cur = self._conn.execute(
            "SELECT MAX(ts) FROM candles WHERE instrument_key=? AND interval=?",
            (instrument_key, interval),
        )
        row = cur.fetchone()[0]
        return datetime.fromisoformat(row) if row else None

    def count(self, instrument_key: str, interval: str) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM candles WHERE instrument_key=? AND interval=?",
            (instrument_key, interval),
        )
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()


# -- downloader ----------------------------------------------------------------


class HistoricalDownloader:
    def __init__(
        self,
        store: CandleStore,
        token_provider: Callable[[], str],
        api_base: str = "https://api.upstox.com",
        rate_limiter: TokenBucket | None = None,
        client: httpx.Client | None = None,
    ):
        self.store = store
        self.token_provider = token_provider
        self.api_base = api_base.rstrip("/")
        self.rate_limiter = rate_limiter or TokenBucket(rate_per_sec=20, burst=20)
        self.client = client or httpx.Client(timeout=60)

    def _get_candles(self, url: str) -> list[list]:
        self.rate_limiter.acquire()
        resp = self.client.get(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token_provider()}",
            },
        )
        if resp.status_code == 401:
            raise AuthError(
                "Upstox rejected the access token (401). Tokens expire daily at "
                "~03:30 IST — run `python main.py auth` to re-login."
            )
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"Unexpected API response for {url}: {body}")
        return body["data"].get("candles", [])

    def _fetch_chunk(
        self, instrument_key: str, spec: IntervalSpec, start: date, end: date
    ) -> list[list]:
        return self._get_candles(
            f"{self.api_base}/v3/historical-candle/{instrument_key}"
            f"/{spec.url_part}/{end.isoformat()}/{start.isoformat()}"
        )

    def fetch_intraday(self, instrument_key: str, interval: str) -> list[list]:
        """Today's candles (the historical endpoint only serves completed days)."""
        spec = parse_interval(interval)
        return self._get_candles(
            f"{self.api_base}/v3/historical-candle/intraday/{instrument_key}"
            f"/{spec.url_part}"
        )

    def download(
        self,
        instrument_key: str,
        interval: str,
        from_date: date,
        to_date: date,
    ) -> int:
        """Fetch the range chunk-by-chunk into the cache. Returns candles stored."""
        spec = parse_interval(interval)
        total = 0
        chunks = chunk_date_range(from_date, to_date, spec.max_days_per_request)
        for start, end in chunks:
            candles = self._fetch_chunk(instrument_key, spec, start, end)
            total += self.store.upsert(instrument_key, interval, candles)
            log.info(
                "historical.chunk_cached",
                instrument=instrument_key,
                interval=interval,
                window=f"{start} -> {end}",  # ASCII: cp1252 console renderers choke on unicode arrows
                candles=len(candles),
            )
        log.info(
            "historical.download_done",
            instrument=instrument_key,
            interval=interval,
            candles=total,
            chunks=len(chunks),
        )
        return total


def build_live_seed(
    store: CandleStore,
    downloader: HistoricalDownloader,
    instrument_key: str,
    interval: str,
    now: datetime | None = None,
    backfill_days_if_empty: int = 90,
) -> pd.DataFrame:
    """Fresh indicator seed for a live session: cache + today's intraday candles.

    Fixes the stale-seed failure mode (2026-07-03: a session seeded with a
    2-day-old cache + started mid-session produced a spurious Supertrend flip
    and a bogus short):
    1. backfills the cache for completed days it is missing,
    2. appends today's candles so far from the intraday endpoint, excluding
       the still-forming bucket (the live builder owns that one).

    Intraday fetch failures degrade to cache-only with a warning — a slightly
    stale seed must not block the session.
    """
    from trading_system.auth.token_store import IST

    now = now or datetime.now(IST)
    today = now.date()

    latest = store.latest_ts(instrument_key, interval)
    backfill_from = (
        latest.date() + timedelta(days=1)
        if latest is not None
        else today - timedelta(days=backfill_days_if_empty)
    )
    if backfill_from < today:  # historical endpoint serves completed days only
        try:
            downloader.download(instrument_key, interval, backfill_from, today)
        except Exception as e:
            log.warning(
                "seed.backfill_failed", instrument=instrument_key, error=str(e)
            )

    seed = store.load(instrument_key, interval)

    try:
        rows = downloader.fetch_intraday(instrument_key, interval)
    except Exception as e:
        log.warning("seed.intraday_failed", instrument=instrument_key, error=str(e))
        rows = []
    if rows:
        intraday = pd.DataFrame(
            rows, columns=["ts", "open", "high", "low", "close", "volume", "oi"]
        )
        intraday["ts"] = pd.to_datetime(intraday["ts"])
        intraday = intraday.set_index("ts").sort_index()[
            ["open", "high", "low", "close", "volume", "oi"]
        ]
        # exclude the still-forming bucket; the live candle builder owns it
        seconds = 60 * int(parse_interval(interval).value)
        epoch = int(now.timestamp())
        forming_start = datetime.fromtimestamp(epoch - epoch % seconds, now.tzinfo)
        intraday = intraday[intraday.index < forming_start]
        seed = pd.concat([seed, intraday])
        seed = seed[~seed.index.duplicated(keep="last")].sort_index()
        log.info(
            "seed.intraday_appended",
            instrument=instrument_key,
            today_candles=len(intraday),
        )
    return seed
