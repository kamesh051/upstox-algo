"""EMA(20/50) crossover filtered by the EMA200 regime.

Long on 20 crossing above 50 while price is above EMA200; short on the mirror.
Exit when the crossover reverses.
"""

from __future__ import annotations

import pandas as pd

from trading_system.strategy.base import Action, Side, Signal, Strategy

SL_ATR_MULT = 2.0
TARGET_ATR_MULT = 3.0


class EmaCrossover(Strategy):
    warmup = 200

    def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
        last, prev = df.iloc[-1], df.iloc[-2]
        crossed_up = prev["ema20"] <= prev["ema50"] and last["ema20"] > last["ema50"]
        crossed_down = prev["ema20"] >= prev["ema50"] and last["ema20"] < last["ema50"]

        if self.position is not None:
            if self.position.side == Side.LONG and crossed_down:
                return Signal(Action.EXIT, reason="EMA20 crossed below EMA50")
            if self.position.side == Side.SHORT and crossed_up:
                return Signal(Action.EXIT, reason="EMA20 crossed above EMA50")
            return Signal.hold()

        atr_ = last["atr14"]
        if crossed_up and last["close"] > last["ema200"]:
            return self.entry(
                Action.BUY,
                stop_loss=last["close"] - SL_ATR_MULT * atr_,
                target=last["close"] + TARGET_ATR_MULT * atr_,
                sentiment=sentiment,
                confidence=0.55,
                reason="EMA20 crossed above EMA50, price above EMA200",
                df=df,
            )
        if crossed_down and last["close"] < last["ema200"]:
            return self.entry(
                Action.SELL,
                stop_loss=last["close"] + SL_ATR_MULT * atr_,
                target=last["close"] - TARGET_ATR_MULT * atr_,
                sentiment=sentiment,
                confidence=0.55,
                reason="EMA20 crossed below EMA50, price below EMA200",
                df=df,
            )
        return Signal.hold()
