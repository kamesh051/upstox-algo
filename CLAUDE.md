# Prompt: Build an Automated Trading System for Upstox

Copy everything below this line into Claude Code (or your AI coding tool) as the project brief.

---

## Project Brief

You are building a production-quality **automated trading system** in Python for the Indian stock market using the **Upstox Uplink API v2**. The system trades intraday equities based on technical indicators combined with a news-sentiment filter. Build it incrementally, phase by phase, with tests at each phase. Do not skip ahead.

## Project Status
- Phase 1 (auth, data download): ✅ complete
- Phase 2 (backtester, 3 strategies): ✅ complete — all 3 strategies NEGATIVE
  after costs on Aug 2025–Jun 2026 (see backtest reports)
- Phase 2.5 (strategy improvement): 🔄 in progress — brief in PHASE-2.5.md
- Current focus: improvement #1 (trade-frequency gates)
- PHASE-UI added — sessions 1–2 (event bus + Ops Overview) prioritized during paper trading week

## Tech Stack (use exactly this)

- Python 3.11+, `asyncio` for the live event loop
- `upstox-python-sdk` (official SDK) for REST; raw `websockets` for the Market Data Feed
- `pandas` + `pandas-ta` for candles and indicators
- SQLite for storage initially (design the data layer so it can swap to TimescaleDB later)
- `APScheduler` for market-hours scheduling (NSE: 9:15–15:30 IST)
- `python-telegram-bot` for trade alerts
- `pydantic` for config and data models, `pytest` for tests
- `structlog` or standard logging with JSON output

## Architecture Requirements

Create this module structure:

```
trading_system/
├── config/            # pydantic settings, instrument list, risk params (YAML)
├── auth/              # OAuth flow, daily token refresh, token persistence
├── data/
│   ├── historical.py  # Download & cache OHLC candles from Upstox REST
│   ├── live_feed.py   # WebSocket tick ingestion with auto-reconnect
│   ├── candles.py     # Tick → 1m/5m/15m candle aggregation
│   └── indicators.py  # RSI, EMA(20/50/200), MACD, Supertrend, VWAP, ATR
├── sentiment/
│   ├── fetchers.py    # News headlines per stock (RSS/NewsAPI, pluggable)
│   └── scorer.py      # FinBERT scoring → float in [-1, +1] per symbol
├── strategy/
│   ├── base.py        # Abstract Strategy: on_candle(df, sentiment) -> Signal
│   ├── rsi_pullback.py
│   ├── ema_crossover.py
│   └── supertrend_follow.py
├── risk/
│   └── manager.py     # Position sizing, SL enforcement, daily loss limit, max positions
├── execution/
│   ├── order_manager.py   # Place/modify/cancel, retries, fill tracking
│   ├── paper_broker.py    # Simulated fills for paper trading
│   └── reconciler.py      # Sync local state with actual Upstox positions
├── backtest/
│   ├── engine.py      # Event-driven, candle-by-candle backtester
│   ├── costs.py       # Brokerage ₹20/order, STT, exchange charges, slippage
│   └── metrics.py     # Win rate, profit factor, max drawdown, Sharpe, equity curve
├── monitor/
│   └── alerts.py      # Telegram notifications, heartbeat
└── main.py            # Entry points: backtest | paper | live
```

## Critical Design Rules

1. **Identical strategy code in backtest and live.** The `Strategy.on_candle(df, sentiment) -> Signal` interface must be the ONLY way strategies receive data. The backtest engine and live engine both call it. No lookahead: the DataFrame passed must never contain the current forming candle's future values.
2. **Signal dataclass** must include: action (BUY/SELL/HOLD/EXIT), stop_loss, target, confidence, reason string (for logging).
3. **Risk manager has veto power.** Every signal passes through it. Rules from config: max capital per trade (e.g., 10%), max concurrent positions (e.g., 3), daily max loss (e.g., 2% of capital → halt trading for the day), mandatory SL on every order.
4. **Never trust local state for positions.** Reconcile with Upstox API after every order event and every N minutes.
5. **WebSocket resilience:** exponential backoff reconnect, resubscribe on reconnect, detect stale feed (no ticks for 30s during market hours → alert).
6. **Paper mode is a first-class citizen:** same code path as live, but orders go to `paper_broker.py` which simulates fills at next tick with configurable slippage.
7. **All money values in paise (int)** internally to avoid float errors; convert at boundaries.
8. **Secrets** (API key/secret, tokens, Telegram token) come from environment variables or `.env`, never hardcoded.
9. **Dashboard code never runs trading logic; engine never imports dashboard code** — see PHASE-UI.md hard rules.

## Backtest Engine Requirements

- Event-driven loop over historical candles (support 5m and 15m timeframes)
- Warm-up period handling (skip signals until indicators are valid, e.g., 200 candles for EMA200)
- Apply cost model on EVERY trade: brokerage (₹20 or 0.05% whichever lower, per order), STT 0.025% on sell side (intraday), exchange txn charges ~0.00297%, SEBI fees, GST 18% on brokerage+txn, stamp duty 0.003% on buy. Plus slippage of 0.03% per side (configurable).
- Force square-off of open positions at 15:15 IST (intraday)
- Output: trades CSV, equity curve PNG (matplotlib), metrics JSON, and a plain-text summary
- Include a walk-forward split utility: train/validate date ranges from config

## Sentiment Layer Requirements

- Pluggable fetcher interface; implement one concrete fetcher using free RSS feeds (Google News RSS per stock symbol/company name)
- Score with FinBERT (`ProsusAI/finbert` via transformers); cache scores per symbol with 15-minute TTL
- Sentiment is a FILTER, not a signal: strategies receive the score and use it to veto trades (e.g., no longs when sentiment < -0.5)
- Must degrade gracefully: if sentiment fetch fails, pass `sentiment=0.0` (neutral) and log a warning — never block trading on sentiment errors

## Upstox API Specifics

- Auth: OAuth authorization-code flow. Build a helper that opens the login URL, accepts the redirect code via a local HTTP listener on the redirect URI, exchanges it for the access token, and saves it. Token expires daily (~3:30 AM IST) — the system must detect 401s and prompt for re-login rather than crash.
- Historical candles: REST endpoint `/v2/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}` — build a downloader that paginates and caches to SQLite/parquet.
- Instrument keys: download the Upstox instruments master (JSON/CSV), build a symbol → instrument_key lookup.
- Live feed: Market Data Feed V3 WebSocket, protobuf-encoded messages — use the official SDK's feeder if available, else implement decode.
- Rate limits: respect documented limits; add a token-bucket rate limiter around REST calls.

## Phases (implement in this order, confirm each works before next)

1. **Phase 1 — Foundation:** config, logging, auth flow, instruments lookup, historical data downloader with caching. Deliverable: CLI command that downloads 1 year of 15m candles for 5 NIFTY50 stocks.
2. **Phase 2 — Backtester:** engine, cost model, metrics, the three strategies. Deliverable: `python main.py backtest --strategy rsi_pullback --from 2025-01-01 --to 2025-12-31` producing a full report.
2.5. **Phase 2.5 — Strategy Improvement (CURRENT PHASE):** See PHASE-2.5.md
     for the full brief. Goal: positive expectancy after costs via trade-frequency
     gates, ADX regime filter, ATR trailing exits, higher-timeframe variants,
     and walk-forward validation. Phases 3+ are BLOCKED until a strategy meets
     the Phase 2.5 success criteria.
3. **Phase 3 — Live data:** WebSocket feed, candle builder, indicator streaming. Deliverable: console printing live 1m candles + indicators for subscribed symbols.
4. **Phase 4 — Paper trading:** wire strategies to live feed with paper broker, Telegram alerts, end-of-day P&L report. Deliverable: full paper session runs unattended 9:15–15:30.
5. **Phase 5 — Sentiment:** fetcher, FinBERT scorer, integrate as filter into strategies; add to backtester via stored historical headline scores if available, else neutral.[deferred — optional after live profitability]
6. **Phase 6 — Live execution:** real order manager with SL orders, reconciler, kill switch (Telegram command `/halt` flattens all positions and stops trading).
7. **Phase UI — Monitoring Dashboard:** see PHASE-UI.md, status: in progress, interleaved with Phase 2.5.

## Testing Requirements

- Unit tests for: candle aggregation, each indicator against known values, cost calculations (verify against a hand-computed example), risk manager limits, signal generation on crafted fixtures
- An integration test that runs the backtester on a small bundled sample dataset and asserts metrics are reproducible
- A "no lookahead" test: assert strategy output is identical whether future candles exist in the dataset or not

## What NOT to do

- Do not build a web UI in v1 (Telegram + logs are enough)
- Do not implement F&O/options in v1 — cash equities intraday only
- Do not auto-optimize/grid-search strategy parameters yet — that comes after walk-forward infrastructure is validated
- Do not place any real order unless mode is explicitly `live` in config AND an environment variable `CONFIRM_LIVE_TRADING=yes` is set

Start with Phase 1. Show me the project scaffold and config design first, then proceed.
