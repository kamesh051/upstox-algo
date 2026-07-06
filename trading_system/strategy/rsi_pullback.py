"""RSI pullback in the direction of the trend.

Long:  price above EMA200 with EMA20>EMA50 (uptrend), RSI(14) dipped below 35
       and is now turning back up -> buy the pullback.
Short: mirror image below EMA200 with RSI recovering down from above 65.
Exit:  RSI reaches the opposite extreme.
"""

from __future__ import annotations

import pandas as pd

from trading_system.strategy.base import Action, Side, Signal, Strategy

RSI_OVERSOLD = 35.0
RSI_OVERBOUGHT = 65.0
RSI_EXIT_LONG = 70.0
RSI_EXIT_SHORT = 30.0
SL_ATR_MULT = 1.5
TARGET_ATR_MULT = 2.5


class RsiPullback(Strategy):
    warmup = 200  # EMA200 must be valid

    def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
        last, prev = df.iloc[-1], df.iloc[-2]

        if self.position is not None:
            if self.position.side == Side.LONG and last["rsi14"] >= RSI_EXIT_LONG:
                return Signal(Action.EXIT, reason=f"RSI {last['rsi14']:.1f} >= {RSI_EXIT_LONG}")
            if self.position.side == Side.SHORT and last["rsi14"] <= RSI_EXIT_SHORT:
                return Signal(Action.EXIT, reason=f"RSI {last['rsi14']:.1f} <= {RSI_EXIT_SHORT}")
            return Signal.hold()

        uptrend = last["close"] > last["ema200"] and last["ema20"] > last["ema50"]
        downtrend = last["close"] < last["ema200"] and last["ema20"] < last["ema50"]
        atr_ = last["atr14"]

        if uptrend and prev["rsi14"] < RSI_OVERSOLD <= last["rsi14"]:
            return self.entry(
                Action.BUY,
                stop_loss=last["close"] - SL_ATR_MULT * atr_,
                target=last["close"] + TARGET_ATR_MULT * atr_,
                sentiment=sentiment,
                confidence=0.6,
                reason=f"uptrend pullback: RSI {prev['rsi14']:.1f}->{last['rsi14']:.1f} crossed {RSI_OVERSOLD}",
                df=df,
            )
        if downtrend and prev["rsi14"] > RSI_OVERBOUGHT >= last["rsi14"]:
            return self.entry(
                Action.SELL,
                stop_loss=last["close"] + SL_ATR_MULT * atr_,
                target=last["close"] - TARGET_ATR_MULT * atr_,
                sentiment=sentiment,
                confidence=0.6,
                reason=f"downtrend rally: RSI {prev['rsi14']:.1f}->{last['rsi14']:.1f} crossed {RSI_OVERBOUGHT}",
                df=df,
            )
        return Signal.hold()
