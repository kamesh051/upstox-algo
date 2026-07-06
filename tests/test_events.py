"""Event bus: ordering, thread-safety surface, and the backpressure hard rule."""

import asyncio
import time as time_mod

import pytest

from trading_system.events import AsyncQueueBus, NullBus, make_event


def test_null_bus_is_noop():
    NullBus().publish(make_event("log", {"x": 1}))  # nothing to assert — no crash


def test_make_event_shape():
    ev = make_event("tick", {"symbol": "X"})
    assert ev.type == "tick"
    assert ev.payload == {"symbol": "X"}
    assert "+05:30" in ev.ts  # IST timestamps


@pytest.mark.asyncio
async def test_publish_subscribe_preserves_order():
    bus = AsyncQueueBus(maxsize=100)
    for i in range(10):
        bus.publish(make_event("log", {"i": i}))
    received = []
    async for ev in bus.subscribe():
        received.append(ev.payload["i"])
        if len(received) == 10:
            break
    assert received == list(range(10))


@pytest.mark.asyncio
async def test_backpressure_drops_never_blocks():
    """PHASE-UI flood test: 10k publishes into a tiny queue must not block."""
    bus = AsyncQueueBus(maxsize=50)
    start = time_mod.perf_counter()
    for i in range(10_000):
        bus.publish(make_event("tick", {"i": i}))
    elapsed = time_mod.perf_counter() - start
    assert elapsed < 1.0, f"publishing 10k events took {elapsed:.3f}s — must be trivial"
    assert bus.dropped == 10_000 - 50
    assert bus._queue.qsize() == 50


def test_publish_without_loop_drops_quietly():
    bus = AsyncQueueBus(maxsize=10)  # constructed outside any event loop
    bus.publish(make_event("log", {"x": 1}))
    # nothing to deliver to yet -> counted as dropped, not raised
    assert bus.dropped == 1


@pytest.mark.asyncio
async def test_publish_from_foreign_thread_delivers():
    import threading

    bus = AsyncQueueBus(maxsize=10)
    bus.publish(make_event("log", {"src": "loop"}))  # binds the loop

    t = threading.Thread(
        target=lambda: bus.publish(make_event("log", {"src": "thread"}))
    )
    t.start()
    t.join()
    await asyncio.sleep(0.05)  # let call_soon_threadsafe run
    assert bus._queue.qsize() == 2


def test_ring_keeps_recent_events():
    bus = AsyncQueueBus(maxsize=5, ring_size=10)
    for i in range(20):
        bus.publish(make_event("log" if i % 2 else "tick", {"i": i}))
    logs = bus.recent(type="log")
    assert len(logs) == 5  # 10 ring slots, half are log-type
    assert logs[-1].payload["i"] == 19
