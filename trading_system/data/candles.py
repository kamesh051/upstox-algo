"""Tick -> candle aggregation and streaming indicators.

``CandleBuilder`` is pure and synchronous (easy to test): feed it Ticks, it
returns candles as they COMPLETE. A candle completes when the first tick of a
later bucket arrives (or on an explicit ``flush``). The forming candle is
never exposed — downstream code only ever sees closed bars, which is what
makes the live path lookahead-free to the same standard as the backtester
(critical design rule 1).

``IndicatorStream`` keeps a rolling window of completed candles per instrument
and recomputes the standard indicator set (data/indicators.add_indicators) on
each append. Recomputing ~500 rows per completed candle is microseconds —
simpler and provably identical to the batch path used in backtests.

Buckets are floored on epoch seconds: for 1/5/15-minute intervals this aligns
with NSE session times (09:15 is :15-aligned; IST's +05:30 offset preserves
quarter-hour alignment). Do NOT add 60minute here without special-casing
session anchoring (09:15 is not epoch-hour aligned).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from trading_system.auth.token_store import IST
from trading_system.data.indicators import add_indicators
from trading_system.data.live_feed import Tick
from trading_system.logging_setup import get_logger

log = get_logger(__name__)

INTERVAL_SECONDS = {"1minute": 60, "5minute": 300, "15minute": 900}


def bucket_start(ts: datetime, interval: str) -> datetime:
    seconds = INTERVAL_SECONDS[interval]
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, IST)


@dataclass
class Candle:
    symbol: str
    instrument_key: str
    interval: str
    ts: datetime  # bucket start, IST
    open: float
    high: float
    low: float
    close: float
    volume: int


class CandleBuilder:
    """Aggregates ticks into candles for several intervals at once.

    ``drop_partial_first``: if the FIRST tick ever seen for a (symbol,
    interval) lands more than ``partial_tolerance_sec`` into its bucket, that
    bucket is partial — we joined mid-candle and missed its earlier trades
    (session started late or after a long disconnect). Emitting it as a
    completed candle corrupts indicators (caused a bogus Supertrend flip and
    a spurious short on 2026-07-03), so it is dropped on completion instead.
    """

    def __init__(
        self,
        intervals: list[str],
        drop_partial_first: bool = False,
        partial_tolerance_sec: float = 10.0,
    ):
        unknown = set(intervals) - set(INTERVAL_SECONDS)
        if unknown:
            raise ValueError(f"Unsupported intervals: {sorted(unknown)}")
        self.intervals = list(intervals)
        self.drop_partial_first = drop_partial_first
        self.partial_tolerance_sec = partial_tolerance_sec
        self._forming: dict[tuple[str, str], Candle] = {}  # (symbol, interval)
        self._partial: set[tuple[str, str]] = set()  # forming candles to drop
        self._seen: set[tuple[str, str]] = set()

    def on_tick(self, tick: Tick) -> list[Candle]:
        """Returns candles completed by this tick (usually 0 or 1 per interval)."""
        completed: list[Candle] = []
        for interval in self.intervals:
            key = (tick.symbol, interval)
            start = bucket_start(tick.ltt, interval)
            forming = self._forming.get(key)

            if forming is None or start > forming.ts:
                if forming is not None:
                    if key in self._partial:
                        self._partial.discard(key)
                        log.info(
                            "candles.partial_first_dropped",
                            symbol=tick.symbol,
                            interval=interval,
                            ts=str(forming.ts),
                        )
                    else:
                        completed.append(forming)
                if (
                    self.drop_partial_first
                    and key not in self._seen
                    and (tick.ltt - start).total_seconds() > self.partial_tolerance_sec
                ):
                    self._partial.add(key)
                self._seen.add(key)
                self._forming[key] = Candle(
                    symbol=tick.symbol,
                    instrument_key=tick.instrument_key,
                    interval=interval,
                    ts=start,
                    open=tick.ltp,
                    high=tick.ltp,
                    low=tick.ltp,
                    close=tick.ltp,
                    volume=tick.ltq,
                )
            elif start == forming.ts:
                forming.high = max(forming.high, tick.ltp)
                forming.low = min(forming.low, tick.ltp)
                forming.close = tick.ltp
                forming.volume += tick.ltq
            else:  # late tick for an already-completed bucket — drop, don't rewrite
                log.warning(
                    "candles.late_tick_dropped",
                    symbol=tick.symbol,
                    interval=interval,
                    tick_ts=str(tick.ltt),
                    forming_ts=str(forming.ts),
                )
        return completed

    def flush(self) -> list[Candle]:
        """Force-complete all forming candles (shutdown / session end)."""
        out = [
            c
            for (sym, itv), c in self._forming.items()
            if (sym, itv) not in self._partial
        ]
        self._forming.clear()
        self._partial.clear()
        return out


class IndicatorStream:
    """Rolling window of completed candles with indicators, per instrument."""

    #: covers a full 1m session (375 bars) plus EMA200-scale warmup context
    DEFAULT_WINDOW = 500

    def __init__(self, seed: pd.DataFrame | None = None, window: int = DEFAULT_WINDOW):
        self.window = window
        if seed is not None and not seed.empty:
            self._raw = seed[["open", "high", "low", "close", "volume"]].tail(window).copy()
            self._raw.index = self._normalize_index(seed.tail(window).index)
        else:
            self._raw = pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"],
                index=pd.DatetimeIndex([], tz=IST),
            )

    @staticmethod
    def _normalize_index(index) -> pd.DatetimeIndex:
        """Coerce any timestamp index to tz-aware IST (ZoneInfo).

        Cached candles from SQLite parse with a FixedOffset(+05:30) tz while
        live candles carry ZoneInfo("Asia/Kolkata"); mixing the two degrades
        the index to object dtype and breaks the daily VWAP reset.
        """
        idx = pd.DatetimeIndex(pd.to_datetime(index))
        return idx.tz_convert(IST) if idx.tz is not None else idx.tz_localize(IST)

    def __len__(self) -> int:
        return len(self._raw)

    def append(self, candle: Candle) -> pd.Series:
        """Add a completed candle; returns its row with indicator columns."""
        ts = pd.Timestamp(candle.ts).tz_convert(IST)
        self._raw.loc[ts] = [
            candle.open, candle.high, candle.low, candle.close, candle.volume,
        ]
        if len(self._raw) > self.window:
            self._raw = self._raw.tail(self.window)
        return add_indicators(self._raw).iloc[-1]

    @property
    def frame(self) -> pd.DataFrame:
        """Completed candles + indicators — the df strategies will receive (Phase 4)."""
        return add_indicators(self._raw)
