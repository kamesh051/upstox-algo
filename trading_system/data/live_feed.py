"""Live tick ingestion from the Upstox Market Data Feed V3 WebSocket.

Uses the official SDK's ``MarketDataStreamerV3`` (protobuf decode + authorize
handshake), bridging its websocket-client callback threads into asyncio via
``loop.call_soon_threadsafe`` onto a queue. Downstream consumers (the candle
builder, Phase 3 #2) read ``async for tick in feed.ticks()``.

Resilience (CLAUDE.md rule 5):
- reconnect with exponential backoff (initial -> max, reset after a session
  that actually opened); the SDK's own fixed-interval auto-reconnect is
  disabled in favor of this supervisor
- resubscribe on reconnect: each attempt builds a fresh streamer seeded with
  the full instrument list, and the SDK subscribes those on socket open
- stale-feed watchdog: no ticks for N seconds during market hours -> alert
  callback (structlog warning now; Telegram hooks in here in Phase 4)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, time

from trading_system.auth.token_store import IST
from trading_system.config.settings import FeedConfig
from trading_system.events import EventBus, NullBus, make_event
from trading_system.logging_setup import get_logger

log = get_logger(__name__)

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def is_market_hours(dt: datetime) -> bool:
    dt = dt.astimezone(IST)
    return dt.weekday() < 5 and MARKET_OPEN <= dt.time() < MARKET_CLOSE


@dataclass(frozen=True)
class Tick:
    instrument_key: str
    symbol: str
    ltp: float  # rupees (market-data domain stays float, like candles)
    ltq: int
    ltt: datetime  # exchange last-trade time, IST
    received_at: datetime


class LiveFeed:
    def __init__(
        self,
        access_token: str,
        key_to_symbol: dict[str, str],
        cfg: FeedConfig,
        streamer_factory: Callable | None = None,
        alert: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        bus: EventBus | None = None,
    ):
        self.cfg = cfg
        self.key_to_symbol = key_to_symbol
        self.bus = bus or NullBus()
        self.status = "connecting"  # open | closed | reconnecting | stale
        self._factory = streamer_factory or (
            lambda: self._build_sdk_streamer(access_token)
        )
        self._alert = alert or (lambda msg: log.warning("feed.alert", message=msg))
        self._clock = clock or (lambda: datetime.now(IST))

        self._queue: asyncio.Queue[Tick] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed: asyncio.Event | None = None
        self._session_opened = False
        self._stopping = False
        self.streamer = None

        self._last_tick_at: datetime | None = None
        self._stale_alerted = False

    # -- SDK wiring ------------------------------------------------------------

    def _build_sdk_streamer(self, access_token: str):
        import upstox_client

        configuration = upstox_client.Configuration()
        configuration.access_token = access_token
        return upstox_client.MarketDataStreamerV3(
            upstox_client.ApiClient(configuration),
            list(self.key_to_symbol),
            self.cfg.mode,
        )

    # -- callbacks (run on the websocket-client thread) ---------------------------

    def _health(self, status: str, **extra) -> None:
        self.status = status
        self.bus.publish(make_event("health", {"component": "feed", "status": status, **extra}))

    def _on_open(self, *args) -> None:
        log.info("feed.open", instruments=len(self.key_to_symbol))
        now = self._clock()
        self._health("open", instruments=len(self.key_to_symbol))

        def mark_open() -> None:
            self._session_opened = True
            self._last_tick_at = now  # watchdog baseline for a fresh session
            self._stale_alerted = False

        self._loop.call_soon_threadsafe(mark_open)

    def _on_message(self, message: dict, *args) -> None:
        feeds = message.get("feeds")
        if not feeds:
            return  # market_info / heartbeat frames
        received_at = self._clock()
        for key, payload in feeds.items():
            symbol = self.key_to_symbol.get(key)
            if symbol is None:
                log.warning("feed.unknown_instrument", instrument_key=key)
                continue
            ltpc = payload.get("ltpc")
            if not ltpc or "ltp" not in ltpc:
                continue
            try:
                # protobuf MessageToDict renders int64 fields as strings
                tick = Tick(
                    instrument_key=key,
                    symbol=symbol,
                    ltp=float(ltpc["ltp"]),
                    ltq=int(ltpc.get("ltq") or 0),
                    ltt=datetime.fromtimestamp(int(ltpc["ltt"]) / 1000, IST),
                    received_at=received_at,
                )
            except (KeyError, TypeError, ValueError) as e:
                log.warning("feed.bad_payload", instrument_key=key, error=str(e))
                continue
            self._loop.call_soon_threadsafe(self._deliver, tick)

    def _deliver(self, tick: Tick) -> None:
        self._last_tick_at = tick.received_at
        self._stale_alerted = False
        self._queue.put_nowait(tick)

    def _on_error(self, *args) -> None:
        log.warning("feed.error", detail=str(args))

    def _on_close(self, *args) -> None:
        log.info("feed.closed", detail=str(args))
        self._health("closed")
        self._loop.call_soon_threadsafe(self._closed.set)

    # -- supervision -----------------------------------------------------------

    async def run(self) -> None:
        """Connect and keep the feed alive until stop(); reconnects with backoff."""
        self._loop = asyncio.get_running_loop()
        self._closed = asyncio.Event()
        watchdog = asyncio.create_task(self._watchdog())
        delay = self.cfg.reconnect_initial_delay
        try:
            while not self._stopping:
                self._closed.clear()
                self._session_opened = False
                self.streamer = self._factory()
                self.streamer.auto_reconnect(False)  # our supervisor instead
                self.streamer.on("open", self._on_open)
                self.streamer.on("message", self._on_message)
                self.streamer.on("error", self._on_error)
                self.streamer.on("close", self._on_close)
                self.streamer.connect()

                await self._closed.wait()
                if self._stopping:
                    break
                if self._session_opened:
                    delay = self.cfg.reconnect_initial_delay
                log.warning("feed.reconnecting", delay_sec=delay)
                self._health("reconnecting", delay_sec=delay)
                await self._sleep(delay)
                delay = min(delay * 2, self.cfg.reconnect_max_delay)
        finally:
            watchdog.cancel()

    async def _sleep(self, seconds: float) -> None:  # patchable in tests
        await asyncio.sleep(seconds)

    def stop(self) -> None:
        self._stopping = True
        try:
            if self.streamer is not None:
                self.streamer.disconnect()
        except Exception as e:  # closing an already-dead socket is fine
            log.debug("feed.disconnect_error", error=str(e))
        if self._loop is not None and self._closed is not None:
            self._loop.call_soon_threadsafe(self._closed.set)

    async def ticks(self) -> AsyncIterator[Tick]:
        """Consumption surface for downstream (candle builder, console printer)."""
        while True:
            yield await self._queue.get()

    # -- stale-feed watchdog -----------------------------------------------------

    def _check_stale(self, now: datetime) -> None:
        if not is_market_hours(now):
            return
        if self._last_tick_at is None or self._stale_alerted:
            return
        silent_for = (now - self._last_tick_at).total_seconds()
        if silent_for > self.cfg.stale_after_seconds:
            self._stale_alerted = True  # re-armed by the next tick
            self._health("stale", silent_for_sec=round(silent_for))
            self._alert(
                f"Stale feed: no ticks for {silent_for:.0f}s during market hours"
            )

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(1)
            self._check_stale(self._clock())
