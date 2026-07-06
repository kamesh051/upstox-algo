"""Dashboard server: snapshot endpoint, /ws envelope, import direction, contract."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.server import create_app
from trading_system.events import AsyncQueueBus, make_event

TRADING_SYSTEM_DIR = Path(__file__).parent.parent / "trading_system"


def make_client():
    bus = AsyncQueueBus(maxsize=100)
    snapshot = {"mode": "paper", "engine_state": "RUNNING", "risk": {}, "logs": []}
    app = create_app(bus, lambda: snapshot, lambda: [{"symbol": "X"}])
    return bus, TestClient(app)


def test_state_endpoint():
    _, client = make_client()
    with client:
        res = client.get("/api/state")
        assert res.status_code == 200
        body = res.json()
        assert body["mode"] == "paper"
        assert body["engine_state"] == "RUNNING"


def test_trades_endpoint():
    _, client = make_client()
    with client:
        assert client.get("/api/trades").json() == [{"symbol": "X"}]


def test_ws_delivers_envelope():
    bus, client = make_client()
    with client:
        with client.websocket_connect("/ws") as ws:
            bus.publish(make_event("health", {"component": "feed", "status": "open"}))
            msg = ws.receive_json()
            assert msg["type"] == "health"
            assert msg["payload"] == {"component": "feed", "status": "open"}
            assert "ts" in msg


def test_frontend_build_served_if_present():
    from dashboard.server import FRONTEND_DIST

    _, client = make_client()
    if not FRONTEND_DIST.exists():
        pytest.skip("frontend not built")
    with client:
        res = client.get("/")
        assert res.status_code == 200
        assert "<div id=\"root\"" in res.text


IMPORT_RE = re.compile(r"^\s*(import dashboard|from dashboard)", re.MULTILINE)


def test_engine_never_imports_dashboard():
    """CLAUDE.md rule 9, enforced mechanically."""
    offenders = [
        p
        for p in TRADING_SYSTEM_DIR.rglob("*.py")
        if IMPORT_RE.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"trading_system must not import dashboard: {offenders}"


def test_contract_snapshot_matches_engine_after_replay():
    """PHASE-UI contract test: after a replayed session, the /api/state snapshot
    numbers must equal the engine's own day report."""
    from types import SimpleNamespace

    from dashboard.state import build_state_provider
    from tests.test_engine_events import run_session
    from trading_system.config.settings import AppConfig, RiskConfig, InstrumentConfig
    from trading_system.auth.token_store import TokenStore

    engine, _ = run_session()
    cfg = AppConfig(
        instruments=[InstrumentConfig(symbol="RELIANCE")],
        risk=RiskConfig(capital_paise=50_000_000),
    )
    bus = AsyncQueueBus(maxsize=10)
    provider = build_state_provider(
        engine,
        SimpleNamespace(status="open"),
        TokenStore("does-not-exist.json"),
        telegram_enabled=False,
        cfg=cfg,
        bus=bus,
        mode="paper",
    )
    snap = provider()
    report = engine.day_report()
    assert snap["risk"]["daily_pnl_paise"] == report["net_pnl_rupees"] * 100
    assert snap["risk"]["trades_today"] == report["trades"]
    assert snap["risk"]["open_positions"] == report["open_positions"]
    assert snap["engine_state"] == ("HALTED" if report["halted"] else "RUNNING")
    assert snap["symbols"]["RELIANCE"]["ltp"] == 89.0  # last replayed tick
