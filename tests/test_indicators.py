"""Indicator unit tests against hand-computed values (see comments for the math)."""

import numpy as np
import pandas as pd
import pytest

from trading_system.data import indicators as ind
from tests.conftest import make_ohlc


def test_ema_hand_computed():
    # span=3 -> alpha=0.5; [2,4,6] -> e1=2, e2=.5*4+.5*2=3, e3=.5*6+.5*3=4.5
    out = ind.ema(pd.Series([2.0, 4.0, 6.0]), period=3)
    assert out.tolist() == [2.0, 3.0, 4.5]


def test_rsi_hand_computed():
    # period=2, prices [1,2,3,2,3]; Wilder ewm(alpha=1/2, adjust=False):
    # gains [_,1,1,0,1] losses [_,0,0,1,0]
    # avg_gain: 1, 1, .5, .75 ; avg_loss: 0, 0, .5, .25
    # RSI: 100 (all gains), 100, 50, 75
    out = ind.rsi(pd.Series([1.0, 2.0, 3.0, 2.0, 3.0]), period=2)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == 100.0
    assert out.iloc[2] == 100.0
    assert out.iloc[3] == pytest.approx(50.0)
    assert out.iloc[4] == pytest.approx(75.0)


def test_rsi_bounds(ohlc_40d):
    out = ind.rsi(ohlc_40d["close"]).dropna()
    assert ((out >= 0) & (out <= 100)).all()


def test_atr_hand_computed():
    # period=2 -> alpha=.5; candles (h,l,c): (12,10,11),(13,11,12),(15,12,14)
    # TR: 2, 2, 3 ; ATR: 2, 2, 2.5
    df = pd.DataFrame(
        {"high": [12.0, 13, 15], "low": [10.0, 11, 12], "close": [11.0, 12, 14]}
    )
    out = ind.atr(df, period=2)
    assert out.tolist() == [2.0, 2.0, 2.5]


def test_macd_identity(ohlc_40d):
    close = ohlc_40d["close"]
    out = ind.macd(close)
    expected_line = ind.ema(close, 12) - ind.ema(close, 26)
    assert np.allclose(out["macd"], expected_line)
    assert np.allclose(out["macd_hist"], out["macd"] - out["macd_signal"])


def test_vwap_resets_daily():
    idx = pd.to_datetime(
        [
            "2026-01-05 09:15:00+05:30",
            "2026-01-05 09:30:00+05:30",
            "2026-01-06 09:15:00+05:30",
        ]
    )
    df = pd.DataFrame(
        {
            "high": [3.0, 6.0, 3.0],
            "low": [1.0, 2.0, 1.0],
            "close": [2.0, 4.0, 2.0],
            "volume": [10, 10, 5],
        },
        index=idx,
    )
    out = ind.vwap(df)
    # day1: tp=2 -> 2 ; then (2*10+4*10)/20 = 3 ; day2 resets: tp=2 -> 2
    assert out.tolist() == [2.0, 3.0, 2.0]


def test_supertrend_direction_and_line():
    n = 120
    up = make_ohlc(days=5, seed=3).iloc[:n].copy()
    # impose a strong steady uptrend on the random walk
    trend = np.linspace(0, 200, n)
    for col in ("open", "high", "low", "close"):
        up[col] = up[col].to_numpy() + trend
    out = ind.supertrend(up)
    tail = out.iloc[-20:]
    assert set(out["st_dir"].unique()) <= {1, -1}
    assert (tail["st_dir"] == 1).all()
    assert (tail["supertrend"] < up["close"].iloc[-20:]).all()

    down = up.copy()
    for col in ("open", "high", "low", "close"):
        down[col] = 2 * float(up[col].iloc[0]) - up[col]  # mirror -> downtrend
    # keep OHLC consistent after mirroring (high/low swap roles)
    down[["high", "low"]] = down[["low", "high"]].to_numpy()
    out_d = ind.supertrend(down)
    assert (out_d.iloc[-20:]["st_dir"] == -1).all()
    assert (out_d.iloc[-20:]["supertrend"] > down["close"].iloc[-20:]).all()


def test_add_indicators_columns(ohlc_40d):
    out = ind.add_indicators(ohlc_40d)
    for col in (
        "ema20", "ema50", "ema200", "rsi14",
        "macd", "macd_signal", "macd_hist",
        "atr14", "vwap", "supertrend", "st_dir",
    ):
        assert col in out.columns, col
    assert len(out) == len(ohlc_40d)
