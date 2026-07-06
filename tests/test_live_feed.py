"""LiveFeed unit tests — no network; a FakeStreamer stands in for the SDK."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from trading_system.auth.token_store import IST
from trading_system.config.settings import FeedConfig
from trading_system.data.live_feed import LiveFeed, Tick, is_market_hours

KEYMAP = {"NSE_EQ|INE002A01018": "RELIANCE", "NSE_EQ|INE467B01029": "TCS"}
TUESDAY_NOON = datetime(2026, 7, 7, 12, 0, tzinfo=IST)  # market hours


class FakeStreamer:
    """Mimics MarketDataStreamerV3's event surface without any socket."""

    def __init__(self):
        self.listeners: dict[str, list] = {}
        self.connect_count = 0
        self.auto_reconnect_args = None
        self.on_connect = None  # test hook: called (self) inside connect()

    def on(self, event, cb):
        self.listeners.setdefault(event, []).append(cb)

    def auto_reconnect(self, enable, *a):
        self.auto_reconnect_args = enable

    def connect(self):
        self.connect_count += 1
        if self.on_connect:
            self.on_connect(self)

    def disconnect(self):
        self.fire("close", 1000, "client disconnect")

    def fire(self, event, *args):
        for cb in self.listeners.get(event, []):
            cb(*args)


def make_feed(factory, clock=None, alert=None, **cfg_overrides) -> LiveFeed:
    cfg = FeedConfig(**cfg_overrides)
    return LiveFeed(
        access_token="tok",
        key_to_symbol=dict(KEYMAP),
        cfg=cfg,
        streamer_factory=factory,
        clock=clock or (lambda: TUESDAY_NOON),
        alert=alert,
    )


def ltpc_message(key="NSE_EQ|INE002A01018", ltp=2950.5, ltq="25", ltt_ms=1751866200000):
    # shaped like protobuf MessageToDict output: int64s rendered as strings
    return {
        "type": "live_feed",
        "feeds": {key: {"ltpc": {"ltp": ltp, "ltt": str(ltt_ms), "ltq": ltq, "cp": 2900.0}}},
        "currentTs": "1751866200100",
    }


async def run_feed_until(feed, condition, timeout=2.0):
    """Run feed.run() while polling for a condition, then stop it cleanly."""
    task = asyncio.create_task(feed.run())
    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while not condition() and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert condition(), "condition never met"
    finally:
        feed.stop()
        await asyncio.wait_for(task, timeout=2)


# -- normalization ------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_normalized_and_delivered():
    fakes: list[FakeStreamer] = []

    def factory():
        f = FakeStreamer()
        f.on_connect = lambda s: (s.fire("open"), s.fire("message", ltpc_message()))
        fakes.append(f)
        return f

    feed = make_feed(factory)
    received: list[Tick] = []

    async def collect():
        async for tick in feed.ticks():
            received.append(tick)
            return

    task = asyncio.create_task(feed.run())
    await asyncio.wait_for(collect(), timeout=2)
    feed.stop()
    await asyncio.wait_for(task, timeout=2)

    t = received[0]
    assert t.symbol == "RELIANCE"
    assert t.ltp == 2950.5
    assert t.ltq == 25  # "25" string coerced
    assert t.ltt.tzinfo is not None
    assert t.ltt == datetime.fromtimestamp(1751866200, IST)
    assert fakes[0].auto_reconnect_args is False  # SDK auto-reconnect disabled


@pytest.mark.asyncio
async def test_unknown_key_and_malformed_payload_skipped():
    def factory():
        f = FakeStreamer()

        def script(s):
            s.fire("open")
            s.fire("message", ltpc_message(key="NSE_EQ|UNKNOWN"))  # not subscribed
            s.fire("message", {"type": "market_info", "marketInfo": {}})  # no feeds
            s.fire("message", {"feeds": {"NSE_EQ|INE467B01029": {"ltpc": {"ltp": "oops", "ltt": "x"}}}})
            s.fire("message", ltpc_message(key="NSE_EQ|INE467B01029", ltp=3100.0))

        f.on_connect = script
        return f

    feed = make_feed(factory)
    received = []

    async def collect():
        async for tick in feed.ticks():
            received.append(tick)
            return

    task = asyncio.create_task(feed.run())
    await asyncio.wait_for(collect(), timeout=2)
    feed.stop()
    await asyncio.wait_for(task, timeout=2)

    # only the one valid TCS tick made it through
    assert [t.symbol for t in received] == ["TCS"]
    assert received[0].ltp == 3100.0


# -- reconnect supervision ------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_backoff_doubles_and_caps():
    fakes: list[FakeStreamer] = []

    def factory():
        f = FakeStreamer()
        # dies immediately without ever opening
        f.on_connect = lambda s: s.fire("close", 1006, "gone")
        fakes.append(f)
        return f

    feed = make_feed(factory, reconnect_initial_delay=1.0, reconnect_max_delay=4.0)
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)
        if len(delays) >= 5:
            feed.stop()

    feed._sleep = fake_sleep
    task = asyncio.create_task(feed.run())
    await asyncio.wait_for(task, timeout=2)

    assert delays == [1.0, 2.0, 4.0, 4.0, 4.0]  # doubles then caps
    # a fresh streamer per attempt -> SDK resubscribes initial keys on open
    assert len(fakes) >= 5
    assert all(f.connect_count == 1 for f in fakes)


@pytest.mark.asyncio
async def test_backoff_resets_after_successful_open():
    fakes: list[FakeStreamer] = []

    def factory():
        f = FakeStreamer()
        # opens successfully, then drops
        f.on_connect = lambda s: (s.fire("open"), s.fire("close", 1006, "drop"))
        fakes.append(f)
        return f

    feed = make_feed(factory, reconnect_initial_delay=1.0, reconnect_max_delay=8.0)
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)
        if len(delays) >= 4:
            feed.stop()

    feed._sleep = fake_sleep
    task = asyncio.create_task(feed.run())
    await asyncio.wait_for(task, timeout=2)

    # every session opened -> backoff resets to initial each time
    assert delays == [1.0, 1.0, 1.0, 1.0]


# -- stale-feed watchdog -----------------------------------------------------------


def stale_feed(last_tick_minutes_ago: float, now: datetime):
    alerts: list[str] = []
    feed = make_feed(lambda: FakeStreamer(), alert=alerts.append, stale_after_seconds=30)
    feed._last_tick_at = now - timedelta(minutes=last_tick_minutes_ago)
    return feed, alerts


def test_watchdog_alerts_when_stale_in_market_hours():
    feed, alerts = stale_feed(2, TUESDAY_NOON)
    feed._check_stale(TUESDAY_NOON)
    assert len(alerts) == 1 and "Stale feed" in alerts[0]
    feed._check_stale(TUESDAY_NOON + timedelta(seconds=5))
    assert len(alerts) == 1  # alerted once, not repeatedly


def test_watchdog_silent_outside_market_hours():
    evening = TUESDAY_NOON.replace(hour=20)
    feed, alerts = stale_feed(10, evening)
    feed._check_stale(evening)
    assert alerts == []
    saturday_noon = datetime(2026, 7, 11, 12, 0, tzinfo=IST)
    feed2, alerts2 = stale_feed(10, saturday_noon)
    feed2._check_stale(saturday_noon)
    assert alerts2 == []


def test_watchdog_rearms_after_recovery():
    feed, alerts = stale_feed(2, TUESDAY_NOON)
    feed._check_stale(TUESDAY_NOON)
    assert len(alerts) == 1
    # a new tick arrives -> _deliver resets the alert latch
    feed._deliver(
        Tick("NSE_EQ|INE002A01018", "RELIANCE", 100.0, 1, TUESDAY_NOON, TUESDAY_NOON)
    )
    feed._check_stale(TUESDAY_NOON + timedelta(minutes=1))
    assert len(alerts) == 2


def test_is_market_hours():
    assert is_market_hours(TUESDAY_NOON)
    assert is_market_hours(datetime(2026, 7, 7, 9, 15, tzinfo=IST))
    assert not is_market_hours(datetime(2026, 7, 7, 9, 14, tzinfo=IST))
    assert not is_market_hours(datetime(2026, 7, 7, 15, 30, tzinfo=IST))
    assert not is_market_hours(datetime(2026, 7, 11, 12, 0, tzinfo=IST))  # Saturday


# -- SDK streamer construction (offline) ---------------------------------------------


def test_sdk_streamer_seeded_with_keys_and_mode():
    feed = make_feed(None, mode="ltpc")  # None factory -> default SDK builder
    streamer = feed._build_sdk_streamer("test-token")
    assert sorted(streamer.instrumentKeys) == sorted(KEYMAP)
    assert streamer.mode == "ltpc"
    # constructor seeds subscriptions -> handle_open resubscribes after reconnect
    assert streamer.subscriptions["ltpc"] == set(KEYMAP)
