# upstox-algo

Automated intraday trading system for NSE equities on the Upstox API.
Built phase by phase — **Phases 1–2 complete** (foundation + backtester).

## Setup

```powershell
uv sync
copy .env.example .env   # then fill in your Upstox API key/secret
```

In the [Upstox developer console](https://account.upstox.com/developer/apps),
create an app with redirect URI `http://localhost:8721/callback` (must match
`UPSTOX_REDIRECT_URI` in `.env`).

## Usage

```powershell
# 1. Daily login (tokens expire ~03:30 IST every day)
uv run python main.py auth

# 2. Refresh the instruments master and resolve configured symbols
uv run python main.py instruments

# 3. Download 1 year of 15-minute candles for the configured 5 NIFTY50 stocks
uv run python main.py download --interval 15minute --days 365

# Or an explicit range / symbol list
uv run python main.py download --from 2025-07-01 --to 2026-07-01 --symbols RELIANCE,TCS

# 4. Stream live ticks (market hours: 09:15-15:30 IST, needs a valid token)
uv run python main.py feed
uv run python main.py feed --symbols RELIANCE,TCS

# 4b. Stream live candles + indicators (the Phase 3 deliverable)
uv run python main.py stream                       # 1m candles, config universe
uv run python main.py stream --intervals 1minute,5minute,15minute

# 4c. Paper trading session (unattended 09:15-15:30; start it before open)
uv run python main.py paper                        # strategy from config (paper.strategy)
uv run python main.py paper --strategy rsi_pullback --symbols RELIANCE,TCS
# Optional Telegram alerts: set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env
# (create a bot via @BotFather; without them alerts stay on the console)

# 4d. Monitoring dashboard (PHASE-UI sessions 1-2)
uv run python main.py paper --dashboard    # live session + http://127.0.0.1:8765
uv run python main.py dashboard-demo       # replay cached days; develop UI offline
# frontend dev:  cd dashboard/frontend && npm install && npm run build

# 5. Backtest a strategy on the cached candles
uv run python main.py backtest --strategy rsi_pullback --from 2025-08-01 --to 2026-06-30
# strategies: rsi_pullback | ema_crossover | supertrend_follow
# writes trades.csv, equity_curve.png, metrics.json, summary.txt under reports/
```

Candles are cached in `data_cache/market_data.sqlite`; re-downloads are
idempotent (upserts keyed on instrument/interval/timestamp).

## Configuration

- `trading_system/config/config.yaml` — instrument universe, risk parameters,
  data/cache settings. All absolute money values are **paise (int)**.
- `.env` — secrets only (API credentials, Telegram token). Never committed.
- Live orders (Phase 6) additionally require `CONFIRM_LIVE_TRADING=yes`.

## Notes

- Historical candles use the **v3** endpoint
  (`/v3/historical-candle/{key}/minutes/15/...`) because v2 does not serve
  15-minute intervals. Minute-granularity requests are chunked into ≤28-day
  windows per API limits, behind a token-bucket rate limiter.
- Access tokens are stored in `.secrets/token.json` and treated as expired at
  the next 03:30 IST boundary; a 401 from the API raises a clear
  "re-run `python main.py auth`" error instead of crashing.

## Tests

```powershell
uv run pytest
```

## Roadmap

1. ✅ Foundation — config, auth, instruments, historical download + cache
2. ✅ Backtester — engine, Indian cost model, metrics, 3 strategies
3. ✅ Live data — WebSocket tick feed (auto-reconnect, stale watchdog), tick→candle builder, streaming indicators
4. ✅ Paper trading — TradingEngine + PaperBroker (next-tick fills), Telegram alerts, EOD report
5. Sentiment — Google News RSS + FinBERT filter [deferred — optional after live profitability]
6. Live execution — order manager, reconciler, kill switch
