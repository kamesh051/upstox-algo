"""No-lookahead guarantees.

1. Indicators are causal: value at row i is identical whether or not rows
   after i exist in the input.
2. Therefore a strategy's signal at candle i is identical when computed on a
   dataset truncated at i vs a slice of the full dataset — future candles
   cannot influence it.
"""

import numpy as np

from trading_system.data.indicators import add_indicators
from trading_system.strategy import STRATEGIES
from tests.conftest import make_ohlc

INDICATOR_COLS = [
    "ema20", "ema50", "ema200", "rsi14",
    "macd", "macd_signal", "macd_hist",
    "atr14", "vwap", "supertrend", "st_dir", "vol_avg20",
]


def test_indicators_are_causal():
    raw = make_ohlc(days=15, seed=11)
    full = add_indicators(raw)
    for i in (60, 150, 249, 300):
        truncated = add_indicators(raw.iloc[: i + 1])
        for col in INDICATOR_COLS:
            a, b = full[col].iloc[i], truncated[col].iloc[i]
            assert np.isclose(a, b, equal_nan=True), (
                f"{col} at row {i}: full={a} truncated={b}"
            )


def test_strategy_signals_ignore_future_candles():
    raw = make_ohlc(days=25, seed=13)
    full = add_indicators(raw)
    for name, strategy_cls in STRATEGIES.items():
        for i in range(220, 320):
            s_full = strategy_cls()
            s_trunc = strategy_cls()
            sig_full = s_full.on_candle(full.iloc[: i + 1], sentiment=0.0)
            sig_trunc = s_trunc.on_candle(
                add_indicators(raw.iloc[: i + 1]), sentiment=0.0
            )
            assert sig_full.action == sig_trunc.action, f"{name} row {i}"
            if sig_full.stop_loss is not None:
                assert np.isclose(sig_full.stop_loss, sig_trunc.stop_loss), f"{name} row {i}"


def test_sentiment_veto_blocks_entries():
    raw = make_ohlc(days=25, seed=13)
    full = add_indicators(raw)
    for strategy_cls in STRATEGIES.values():
        for i in range(220, 320):
            strat = strategy_cls()
            sig = strat.on_candle(full.iloc[: i + 1], sentiment=0.0)
            if sig.action.value == "BUY":
                vetoed = strategy_cls().on_candle(full.iloc[: i + 1], sentiment=-0.9)
                assert vetoed.action.value == "HOLD"
                assert "sentiment veto" in vetoed.reason
            elif sig.action.value == "SELL":
                vetoed = strategy_cls().on_candle(full.iloc[: i + 1], sentiment=0.9)
                assert vetoed.action.value == "HOLD"
