# Phase 2.5 — Improvement #1: Trade-Frequency Gates — Before/After

Period: 2025-08-01 to 2026-06-30, 15m candles, 5 NIFTY50 stocks, Rs 5,00,000 capital.
Gates: >=2 of 3 confirmations (VWAP side, volume >1.5x avg20, MACD-hist sign),
8-candle cooldown after exit, max 2 trades/symbol/day, max 6 trades/day.
Baseline (`--no-gates`) verified bit-identical to the Phase 2 runs.

| Strategy | Variant | Trades | Win % | Avg win Rs | Avg loss Rs | Expectancy Rs/trade | Profit factor | Max DD % | Costs Rs | Net P&L Rs |
|---|---|---|---|---|---|---|---|---|---|---|
| rsi_pullback | baseline | 100 | 36.0 | 276.81 | -288.14 | -84.75 | 0.54 | -1.761 | 6455.83 | -8475.49 |
| rsi_pullback | gated | 7 | 28.57 | 202.07 | -305.52 | -160.49 | 0.265 | -0.342 | 451.9 | -1123.44 |
| ema_crossover | baseline | 263 | 33.84 | 262.62 | -281.03 | -97.06 | 0.478 | -5.195 | 16969.78 | -25526.78 |
| ema_crossover | gated | 216 | 33.33 | 261.77 | -269.6 | -92.47 | 0.485 | -4.126 | 13936.83 | -19974.3 |
| supertrend_follow | baseline | 548 | 47.81 | 235.46 | -337.43 | -63.53 | 0.639 | -7.609 | 35368.57 | -34813.77 |
| supertrend_follow | gated | 424 | 47.17 | 236.52 | -310.32 | -52.38 | 0.681 | -4.989 | 27365.01 | -22208.04 |

- **rsi_pullback**: 100 -> 7 trades, expectancy Rs -84.75 -> Rs -160.49/trade, costs Rs 6,455.83 -> Rs 451.9, net Rs -8,475.49 -> Rs -1,123.44

- **ema_crossover**: 263 -> 216 trades, expectancy Rs -97.06 -> Rs -92.47/trade, costs Rs 16,969.78 -> Rs 13,936.83, net Rs -25,526.78 -> Rs -19,974.3

- **supertrend_follow**: 548 -> 424 trades, expectancy Rs -63.53 -> Rs -52.38/trade, costs Rs 35,368.57 -> Rs 27,365.01, net Rs -34,813.77 -> Rs -22,208.04

## Verdict

Gates improved expectancy and cut losses for all three strategies, but none
reached positive expectancy. Per PHASE-2.5.md this is improvement #1 of 6;
regime filter (#2), asymmetric exits (#3) and higher timeframes (#4) are the
next levers. rsi_pullback fell to 7 trades — below the 30-trade minimum, so
its numbers are not meaningful; the volume-surge confirmation rarely aligns
with pullback entries and should be revisited in improvement #2.