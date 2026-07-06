# PHASE-UI.md — Real-Time Monitoring Dashboard

Brief for Claude Code. A local, single-user web dashboard to monitor the trading
engine in real time. Read-only first; control actions come last, gated.

---

## Goals & non-goals

GOALS
- See at a glance: engine health, positions, P&L, signals, and why decisions happened
- Real-time (<1s from engine event to screen), runs on localhost alongside the engine
- Zero interference: UI crash or refresh must never affect the trading engine

NON-GOALS (v1)
- No strategy editing, no parameter tuning from UI, no backtest launcher UI
- No multi-user auth, no cloud deployment, no mobile app (Telegram already covers alerts)
- Not a charting terminal — TradingView exists; we embed a lightweight chart only

## Architecture

```
┌────────────────┐   events (pub)   ┌─────────────────┐   WS + REST    ┌──────────┐
│ Trading Engine │ ───────────────► │ Dashboard Server│ ─────────────► │ Browser  │
│ (existing)     │  SQLite (shared) │ FastAPI         │                │ React    │
└────────────────┘                  └─────────────────┘                └──────────┘
```

- **Decoupling rule:** the engine NEVER imports dashboard code. It publishes events
  to a local queue (start with `sqlite` polling or a simple `asyncio` pub-sub in
  the same process behind an interface; design so it can move to Redis later).
- **Dashboard server (FastAPI):** owns a WebSocket endpoint (`/ws`) that pushes
  JSON events to the browser, plus REST endpoints for initial state and history.
- **Frontend:** React + Vite + Tailwind, single-page, dark theme default.
  Recharts for equity/P&L sparklines. No state library beyond React context;
  server is the source of truth.

## Event contract (single JSON envelope on /ws)

```json
{ "type": "tick|candle|signal|order|position|risk|health|log",
  "ts": "2026-07-06T10:30:05+05:30",
  "payload": { } }
```

REST endpoints: `GET /api/state` (full snapshot for page load/refresh),
`GET /api/trades?date=`, `GET /api/journal?date=`, `GET /api/candles?symbol=&interval=`.
All money values arrive in paise; the frontend formats to ₹.

## Screens (build in this order)

### 1. Ops Overview (the "is everything OK" screen — most important)
- Status bar: engine state (RUNNING/HALTED), mode badge (PAPER in yellow / LIVE in
  red — unmissable), market phase (pre-open/open/closed), current time IST
- Health tiles: WebSocket feed (last tick age per symbol), broker API health,
  token expiry countdown, Telegram bot status, DB writable
- Risk tiles: daily P&L vs daily loss limit (progress bar toward −2% halt),
  open positions count vs max, trades today vs cap
- Live log tail (last 50 events, filterable by level), auto-scroll toggle

### 2. Positions & Orders
- Open positions table: symbol, qty, avg price, LTP (live), unrealized P&L
  (green/red, live), current SL level, distance to SL in ATRs, time in trade
- Orders today: canonical status chips (OPEN/FILLED/CANCELLED/REJECTED),
  fill price vs signal price (slippage column), strategy tag
- Trade timeline strip: each round-trip as entry→exit with net ₹ after costs

### 3. Signals & Decisions (the "why" screen — your debugging superpower)
- Every strategy evaluation per candle close, including HOLDs: timestamp, symbol,
  action, and the named reasons/failed gates from the engine
  (e.g. "HOLD — volume gate failed 1.2x<1.5x; cooldown 3 candles left")
- Veto log from risk manager with reasons
- This screen is rendered entirely from data the engine already logs — if
  something isn't visible here, the fix is better engine logging, not UI logic

### 4. Charts (lightweight)
- Per-symbol candlestick (use `lightweight-charts` by TradingView, free) with
  indicator overlays (supertrend, VWAP, EMA) and markers for entries/exits/signals
- Purpose: visual confirmation that signals sit where the backtest logic says
  they should — not for manual trading decisions

### 5. EOD & History
- Daily report view mirroring the Telegram EOD: trades, win/loss, costs, expectancy
- Calendar heatmap of daily P&L; equity curve since paper start
- Candle-diff report viewer: show the daily live-vs-official candle comparison
  result prominently (green tick / red mismatch)

### 6. Controls (LAST, and gated)
- v1 controls only: `/halt` (flatten + stop, with type-to-confirm modal exactly
  like Telegram), pause new entries, resume
- Rules: controls hit the SAME code path as Telegram commands (one control plane),
  require typing the word HALT, are disabled entirely unless dashboard is served
  on localhost, and every control action is journaled
- No order placement from UI in v1. Ever. That path stays engine-only.

## Build order & estimates (Claude Code sessions)

1. Event bus interface in engine + FastAPI skeleton + /ws pushing health & log
   events + React shell with status bar ................................ 1 session
2. Ops Overview complete (tiles + log tail) ............................ 1 session
3. Positions/Orders screens with live updates .......................... 1 session
4. Signals & Decisions screen .......................................... 1 session
5. Charts with markers ................................................. 1–2 sessions
6. EOD/History + candle-diff viewer .................................... 1 session
7. Controls with confirmation flow ..................................... 1 session

Definition of done per screen: renders from a cold `GET /api/state`, updates live
via /ws, survives browser refresh mid-session, and has no polling loops except
the log tail fallback.

## Hard rules

- UI reads state; it never computes trading logic. If the UI must calculate
  something (e.g., distance-to-SL), it's presentation math only
- Engine performance is sacred: event publishing must be fire-and-forget,
  non-blocking, and drop events under backpressure rather than slow the engine
- The dashboard process can die and restart at any time with zero engine impact
  (state snapshot rebuilds it)
- Mode badge (PAPER/LIVE) visible on every screen, always
- No external network calls from the dashboard (fully local; charts library
  bundled, not CDN)

## Testing

- Contract test: replay a recorded day of engine events through the bus; assert
  final UI state snapshot equals the engine's EOD state
- Backpressure test: flood 10k events; engine loop latency must not degrade
- Kill test: kill dashboard mid-day; engine trades on; restart rebuilds state
