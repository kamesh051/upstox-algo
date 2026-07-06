"""Event bus: the seam between the trading engine and the dashboard.

The engine (and feed, and logging) publish typed events through the
``EventBus`` interface defined HERE, inside trading_system — the dashboard
package imports this module, never the reverse (CLAUDE.md rule 9).

Hard rule (PHASE-UI): publishing is fire-and-forget. It never blocks, never
raises, and drops events under backpressure rather than slowing the engine.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from trading_system.auth.token_store import IST

EVENT_TYPES = ("tick", "candle", "signal", "order", "position", "risk", "health", "log")


@dataclass(frozen=True)
class Event:
    type: str  # one of EVENT_TYPES
    ts: str  # ISO8601 IST
    payload: dict


def make_event(type: str, payload: dict, ts: datetime | None = None) -> Event:
    return Event(
        type=type,
        ts=(ts or datetime.now(IST)).isoformat(),
        payload=payload,
    )


class EventBus(Protocol):
    def publish(self, event: Event) -> None: ...


class NullBus:
    """Default bus: publishing costs one no-op call. Used when no dashboard."""

    def publish(self, event: Event) -> None:
        pass


@dataclass
class AsyncQueueBus:
    """In-process pub-sub on a bounded asyncio.Queue.

    - ``publish`` is safe from any thread: on the bus's loop it enqueues
      directly; from other threads (e.g. websocket callbacks) it trampolines
      via ``call_soon_threadsafe``; with no loop known yet it counts a drop.
    - A bounded queue + drop counter implement the backpressure rule.
    - ``ring`` keeps the most recent events for the /api/state snapshot.
    """

    maxsize: int = 2000
    ring_size: int = 300
    dropped: int = 0
    _queue: asyncio.Queue = field(init=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)
    ring: deque = field(init=False)

    def __post_init__(self) -> None:
        self._queue = asyncio.Queue(maxsize=self.maxsize)
        self.ring = deque(maxlen=self.ring_size)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def _put(self, event: Event) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1

    def publish(self, event: Event) -> None:
        self.ring.append(event)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            if self._loop is None:
                self._loop = running
            if running is self._loop:
                self._put(event)
                return
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._put, event)
        else:  # no loop to deliver to yet — drop, never block
            self.dropped += 1

    async def subscribe(self) -> AsyncIterator[Event]:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        while True:
            yield await self._queue.get()

    def recent(self, type: str | None = None, limit: int = 200) -> list[Event]:
        events = [e for e in self.ring if type is None or e.type == type]
        return events[-limit:]
