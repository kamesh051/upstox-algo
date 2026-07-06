"""Supertrend(10, 3) direction-flip follower.

Enter when the Supertrend direction flips; stop sits at the Supertrend line
itself; exit when the direction flips back.
"""

from __future__ import annotations

import pandas as pd

from trading_system.strategy.base import Action, Side, Signal, Strategy

TARGET_ATR_MULT = 2.0


class SupertrendFollow(Strategy):
    warmup = 50  # Supertrend(10) needs ~3x period for ATR to settle

    def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
        last, prev = df.iloc[-1], df.iloc[-2]
        flipped_up = prev["st_dir"] == -1 and last["st_dir"] == 1
        flipped_down = prev["st_dir"] == 1 and last["st_dir"] == -1

        if self.position is not None:
            if self.position.side == Side.LONG and last["st_dir"] == -1:
                return Signal(Action.EXIT, reason="Supertrend flipped down")
            if self.position.side == Side.SHORT and last["st_dir"] == 1:
                return Signal(Action.EXIT, reason="Supertrend flipped up")
            return Signal.hold()

        atr_ = last["atr14"]
        if flipped_up:
            return self.entry(
                Action.BUY,
                stop_loss=last["supertrend"],
                target=last["close"] + TARGET_ATR_MULT * atr_,
                sentiment=sentiment,
                confidence=0.5,
                reason="Supertrend flipped up",
                df=df,
            )
        if flipped_down:
            return self.entry(
                Action.SELL,
                stop_loss=last["supertrend"],
                target=last["close"] - TARGET_ATR_MULT * atr_,
                sentiment=sentiment,
                confidence=0.5,
                reason="Supertrend flipped down",
                df=df,
            )
        return Signal.hold()
