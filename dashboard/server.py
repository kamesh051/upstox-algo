"""Dashboard server: FastAPI app pushing engine events over WebSocket.

Import direction is one-way: this package imports trading_system; nothing in
trading_system may import dashboard (CLAUDE.md rule 9, enforced by a test).

The server is read-only presentation. It consumes the event bus, fans events
out to browser clients (each client has its own bounded queue — a slow tab
drops its own events, never anyone else's, and never the engine's), and
serves a state snapshot for cold loads/refreshes.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from trading_system.events import AsyncQueueBus
from trading_system.logging_setup import get_logger

log = get_logger(__name__)

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

StateProvider = Callable[[], dict]
TradesProvider = Callable[[], list[dict]]

CLIENT_QUEUE_SIZE = 500


def create_app(
    bus: AsyncQueueBus,
    state_provider: StateProvider,
    trades_provider: TradesProvider = lambda: [],
) -> FastAPI:
    clients: set[asyncio.Queue] = set()

    async def fanout() -> None:
        async for event in bus.subscribe():
            for q in list(clients):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass  # slow client loses its own events; engine unaffected

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(fanout())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    app = FastAPI(title="upstox-algo dashboard", lifespan=lifespan)

    @app.get("/api/state")
    async def state() -> dict:
        return state_provider()

    @app.get("/api/trades")
    async def trades() -> list[dict]:
        return trades_provider()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        clients.add(q)
        try:
            while True:
                event = await q.get()
                await websocket.send_json(
                    {"type": event.type, "ts": event.ts, "payload": event.payload}
                )
        except WebSocketDisconnect:
            pass
        except Exception as e:  # a dying client must never propagate upward
            log.debug("dashboard.ws_closed", error=str(e))
        finally:
            clients.discard(q)

    if FRONTEND_DIST.exists():  # bundled build; API/WS routes above take precedence
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
    return app


async def serve(app: FastAPI, host: str, port: int) -> None:
    """Run uvicorn inside the current loop. Failures are logged, never raised —
    the trading session must survive any dashboard death."""
    # ws="wsproto" explicitly: uvicorn's default impl needs the legacy API that
    # modern `websockets` releases removed, silently downgrading /ws to HTTP 404
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", ws="wsproto")
    server = uvicorn.Server(config)
    try:
        log.info("dashboard.serving", url=f"http://{host}:{port}")
        await server.serve()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.error("dashboard.server_died", error=str(e))
