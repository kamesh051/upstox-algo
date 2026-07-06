# Prompt: Phase 2.5 — Strategy Improvement (Expectancy Optimization)

Paste this into Claude Code in your project folder.

---

## Context

Phases 1–2 are complete. Backtest results on real data (Aug 2025 → Jun 2026, 5 stocks, ₹5L) show all three strategies losing money after realistic costs:

- rsi_pullback: 100 trades, 36% WR, −₹8,475
- ema_crossover: 263 trades, 34% WR, −₹25,527
- supertrend_follow: 548 trades, 48% WR, −₹34,814 (₹35,369 in costs — gross P&L was nearly flat)

Diagnosis: excessive trade frequency, no market-regime awareness, symmetric fixed targets that cap winners, and a cost-hostile 15-minute timeframe.

## Goal — read carefully

The target is POSITIVE EXPECTANCY per trade after costs, NOT a high win rate. Do not optimize for win rate. Optimize for: expectancy = (win% × avg_win) − (loss% × avg_loss) − avg_cost_per_trade. A 40% win rate with 2.5:1 reward:risk beats a 70% win rate with 0.5:1. Report expectancy, profit factor, max drawdown, and trade count for every experiment.

## Improvements to implement (in this order)

### 1. Trade-frequency reduction (biggest lever — supertrend paid ₹35k in costs)
- Add a minimum-conditions gate: signals only fire when ≥2 independent confirmations align (e.g., supertrend flip + price above VWAP + volume > 1.5× 20-candle average)
- Add a cooldown: after any exit on a symbol, no new entry on that symbol for N candles (default 8, configurable)
- Add max trades per symbol per day (default 2) and max total trades per day (default 6)
- Rerun all three strategies with only these gates and report the before/after comparison table

### 2. Market-regime filter (fixes ema_crossover whipsaw)
- Implement an ADX(14)-based regime classifier on the 15m chart: TRENDING if ADX > 25, RANGING if ADX < 20, TRANSITION otherwise
- Also compute a daily-timeframe trend bias: price vs EMA50 on daily candles (bullish/bearish/neutral)
- Rules: trend-following strategies (ema_crossover, supertrend) may only trade in TRENDING regime AND in the direction of the daily bias. Mean-reversion (rsi_pullback) may only trade in RANGING regime.
- Report how many losing trades each filter eliminated vs winning trades it cost

### 3. Asymmetric exits — let winners run (fixes capped reward:risk)
- Replace fixed targets with: initial SL at 1.2 × ATR(14) below entry; when price moves +1 × ATR in favor, move SL to breakeven; thereafter trail with supertrend or 2 × ATR chandelier trailing stop
- Keep the 15:15 IST square-off
- Add partial exit option (book 50% at +1.5 × ATR, trail the rest) as a configurable variant; test both

### 4. Higher timeframe variant (attacks the cost problem structurally)
- Add a 60-minute timeframe version of each strategy and a swing variant (daily candles, 2–10 day holds, CNC delivery — recompute the cost model for delivery: zero brokerage on Upstox delivery, STT 0.1% both sides, DP charges ₹20 per sell)
- Compare 15m vs 60m vs daily on identical logic. Hypothesis to test: fewer, larger moves beat many small ones after costs.

### 5. Walk-forward validation (mandatory before believing anything)
- Split: optimize/tune on Aug 2025–Feb 2026, validate untouched on Mar–Jun 2026
- Any configuration whose validation expectancy is <60% of its training expectancy is flagged OVERFIT and rejected
- Also run each surviving config on 5 DIFFERENT stocks (not in the original set) as an out-of-sample robustness check

### 6. Reporting
- Produce a single comparison report (markdown + CSV): every variant × timeframe × filter combination with trades, win rate, avg win, avg loss, expectancy per trade (₹), profit factor, max DD, total costs, net P&L
- Rank by expectancy per trade, minimum 30 trades in validation period to qualify
- End the report with the top 3 candidates for paper trading and an explicit statement of their validation-period expectancy

## Hard rules

- NO grid-searching more than 3 values per parameter (overfitting guard)
- NO reporting results without costs
- NO cherry-picking date ranges — all results on the full split defined above
- If NOTHING achieves positive validation expectancy, say so plainly and recommend the swing/delivery timeframe path instead of forcing intraday to work
- Do not modify the cost model to make results look better

## Success criteria for this phase

A strategy qualifies for Phase 4 paper trading if, on the untouched validation period: expectancy per trade > ₹0 after costs, profit factor > 1.3, max drawdown < 8%, and ≥30 trades. Win rate is reported but is NOT a criterion.

Start with improvement #1, show me the before/after table, then proceed.
