"""Snapshot assembly for GET /api/state — read-only presentation aggregation.

All numbers come straight off engine/risk/feed objects; nothing here computes
trading decisions (PHASE-UI hard rule). Money stays in paise; the frontend
formats.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time
from pathlib import Path

from trading_system.auth.token_store import IST, TokenStore, _expiry_after
from trading_system.data.live_feed import MARKET_CLOSE, MARKET_OPEN
from trading_system.events import AsyncQueueBus


def market_phase(now: datetime) -> str:
    now = now.astimezone(IST)
    if now.weekday() >= 5:
        return "closed"
    if now.time() < time(9, 0):
        return "closed"
    if now.time() < MARKET_OPEN:
        return "pre-open"
    if now.time() < MARKET_CLOSE:
        return "open"
    return "closed"


def _token_expires_in_sec(token_store: TokenStore, now: datetime) -> int | None:
    import json

    if not token_store.path.exists():
        return None
    try:
        payload = json.loads(token_store.path.read_text(encoding="utf-8"))
        issued = datetime.fromisoformat(payload["issued_at"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    return max(0, int((_expiry_after(issued) - now.astimezone(IST)).total_seconds()))


def _db_writable(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(db_path, timeout=1)
        conn.execute("PRAGMA user_version")
        conn.close()
        return True
    except sqlite3.Error:
        return False


def build_state_provider(
    engine,
    feed,
    token_store: TokenStore,
    telegram_enabled: bool,
    cfg,
    bus: AsyncQueueBus,
    mode: str = "paper",
):
    def provider() -> dict:
        now = datetime.now(IST)
        symbols = {}
        for sym in engine.streams:
            last = engine.last_tick_by_symbol.get(sym)
            symbols[sym] = {
                "ltp": last.ltp if last else None,
                "last_tick_ts": last.received_at.isoformat() if last else None,
                "last_tick_age_sec": (
                    round((now - last.received_at).total_seconds(), 1) if last else None
                ),
            }
        risk = engine.risk
        gates = cfg.risk.gates
        return {
            "mode": mode,
            "engine_state": "HALTED" if risk.halted else "RUNNING",
            "market_phase": market_phase(now),
            "now": now.isoformat(),
            "strategy": engine.strategy_cls.__name__,
            "interval": engine.interval,
            "symbols": symbols,
            "health": {
                "feed": feed.status,
                "token_expires_in_sec": _token_expires_in_sec(token_store, now),
                "telegram_enabled": telegram_enabled,
                "db_writable": _db_writable(cfg.data.db_path),
                "events_dropped": bus.dropped,
            },
            "risk": {
                "daily_pnl_paise": risk.daily_realized_pnl_paise,
                "daily_loss_limit_paise": -int(
                    cfg.risk.capital_paise * cfg.risk.daily_max_loss_pct
                ),
                "capital_paise": cfg.risk.capital_paise,
                "open_positions": len(engine.open_positions),
                "max_positions": cfg.risk.max_concurrent_positions,
                "trades_today": len(engine.trades),
                "max_trades_per_day": gates.max_trades_per_day if gates.enabled else None,
                "halted": risk.halted,
            },
            "logs": [
                {"ts": e.ts, **e.payload} for e in bus.recent(type="log", limit=200)
            ],
        }

    return provider


def build_trades_provider(engine):
    def provider() -> list[dict]:
        return [
            {
                "symbol": t.symbol, "side": t.side.value, "qty": t.qty,
                "entry_time": t.entry_time.isoformat(), "entry_price": t.entry_price,
                "exit_time": t.exit_time.isoformat(), "exit_price": t.exit_price,
                "gross_pnl_paise": t.gross_pnl_paise, "costs_paise": t.costs_paise,
                "net_pnl_paise": t.net_pnl_paise,
                "entry_reason": t.entry_reason, "exit_reason": t.exit_reason,
            }
            for t in engine.trades
        ]

    return provider
