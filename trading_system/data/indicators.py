"""Technical indicators, hand-rolled on pandas/numpy.

(pandas-ta is abandoned and breaks on numpy>=2, so these are implemented
directly — every one is unit-tested against hand-computed values.)

All indicators are strictly causal: row i depends only on rows <= i. That
property is what lets the backtester precompute indicator columns on the full
dataset and then slice — the no-lookahead test in tests/test_no_lookahead.py
asserts it holds.

Conventions:
- RSI/ATR use Wilder smoothing seeded recursively from the first value
  (ewm(alpha=1/n, adjust=False)), not SMA-seeded; values converge after ~3n rows.
- VWAP resets at each session (calendar day) — it is an intraday indicator.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    # all-gain stretches: avg_loss == 0 → RSI 100
    out = out.where(avg_loss != 0, 100.0)
    out.iloc[0] = np.nan  # no delta on the first row
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {"macd": line, "macd_signal": sig, "macd_hist": line - sig}
    )


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP: cumulative (typical price x volume) / volume, reset daily."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    day = pd.Series(df.index.date, index=df.index)
    return pv.groupby(day).cumsum() / df["volume"].groupby(day).cumsum()


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Returns columns: supertrend (the line), st_dir (+1 up / -1 down)."""
    hl2 = (df["high"] + df["low"]) / 2
    atr_ = atr(df, period)
    upper = (hl2 + multiplier * atr_).to_numpy()
    lower = (hl2 - multiplier * atr_).to_numpy()
    close = df["close"].to_numpy()

    n = len(df)
    f_upper = upper.copy()
    f_lower = lower.copy()
    direction = np.ones(n, dtype=np.int64)
    line = np.full(n, np.nan)

    for i in range(1, n):
        # Final bands only ratchet: they tighten with the trend, never loosen,
        # unless price closed beyond them on the previous candle.
        if upper[i] < f_upper[i - 1] or close[i - 1] > f_upper[i - 1]:
            f_upper[i] = upper[i]
        else:
            f_upper[i] = f_upper[i - 1]
        if lower[i] > f_lower[i - 1] or close[i - 1] < f_lower[i - 1]:
            f_lower[i] = lower[i]
        else:
            f_lower[i] = f_lower[i - 1]

        if close[i] > f_upper[i]:
            direction[i] = 1
        elif close[i] < f_lower[i]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        line[i] = f_lower[i] if direction[i] == 1 else f_upper[i]

    return pd.DataFrame(
        {"supertrend": line, "st_dir": direction}, index=df.index
    )


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Standard indicator set used by all strategies. Input needs OHLCV columns."""
    out = df.copy()
    out["ema20"] = ema(df["close"], 20)
    out["ema50"] = ema(df["close"], 50)
    out["ema200"] = ema(df["close"], 200)
    out["rsi14"] = rsi(df["close"], 14)
    out = out.join(macd(df["close"]))
    out["atr14"] = atr(df, 14)
    out["vwap"] = vwap(df)
    # shifted so a candle's own volume is not part of its baseline
    out["vol_avg20"] = df["volume"].rolling(20).mean().shift(1)
    out = out.join(supertrend(df))
    return out
