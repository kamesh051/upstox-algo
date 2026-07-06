"""Strategy interface shared verbatim by the backtester and the live engine.

``on_candle(df, sentiment) -> Signal`` is the ONLY way a strategy receives
data. The DataFrame contains completed candles up to and including the candle
that just closed — never the forming candle — with indicator columns already
attached (see data/indicators.add_indicators). Fills happen at the NEXT
candle's open, so acting on the last row is lookahead-free.

Engines set ``self.position`` (or None) before each call so strategies can
emit EXITs; strategies must not mutate it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import pandas as pd

from trading_system.config.settings import GatesConfig


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    EXIT = "EXIT"


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class Signal:
    action: Action
    stop_loss: float | None = None  # rupees; mandatory for BUY/SELL entries
    target: float | None = None  # rupees
    confidence: float = 0.5  # 0..1
    reason: str = ""

    @classmethod
    def hold(cls, reason: str = "") -> "Signal":
        return cls(action=Action.HOLD, reason=reason)


@dataclass
class PositionState:
    """What a strategy is allowed to know about its open position."""

    symbol: str
    side: Side
    qty: int
    entry_price: float
    stop_loss: float
    target: float | None
    entry_time: datetime


# Sentiment is a veto filter, not a signal (project rule): longs are blocked on
# strongly negative news, shorts on strongly positive. 0.0 = neutral = no veto.
SENTIMENT_VETO_THRESHOLD = 0.5


class Strategy(ABC):
    #: candles required before signals are meaningful (engine skips until then)
    warmup: int = 200

    def __init__(self, gates: GatesConfig | None = None) -> None:
        self.position: PositionState | None = None
        # trade-frequency confirmation gate; None or enabled=False = no gating
        self.gates = gates

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
        """df: completed candles + indicators, oldest→newest. Last row = now."""

    def sentiment_vetoes(self, action: Action, sentiment: float) -> bool:
        if action == Action.BUY and sentiment < -SENTIMENT_VETO_THRESHOLD:
            return True
        if action == Action.SELL and sentiment > SENTIMENT_VETO_THRESHOLD:
            return True
        return False

    def count_confirmations(
        self, df: pd.DataFrame, action: Action
    ) -> tuple[int, list[str]]:
        """Independent, direction-aware confirmation checks on the last candle.

        Returns (number passed, names of failed checks). NaN indicator values
        (e.g. vol_avg20 during its 20-candle warmup) count as failed.
        """
        last = df.iloc[-1]
        is_long = action == Action.BUY
        mult = self.gates.volume_surge_mult if self.gates else 1.5
        checks = {
            "vwap": bool(
                last["close"] > last["vwap"] if is_long else last["close"] < last["vwap"]
            ),
            "volume": bool(last["volume"] > mult * last["vol_avg20"]),
            "macd": bool(
                last["macd_hist"] > 0 if is_long else last["macd_hist"] < 0
            ),
        }
        failed = [name for name, ok in checks.items() if not ok]
        return len(checks) - len(failed), failed

    def entry(
        self,
        action: Action,
        stop_loss: float,
        target: float | None,
        sentiment: float,
        confidence: float,
        reason: str,
        df: pd.DataFrame | None = None,
    ) -> Signal:
        """Build an entry signal: sentiment veto, then the confirmation gate."""
        if self.sentiment_vetoes(action, sentiment):
            return Signal.hold(f"sentiment veto ({sentiment:+.2f}): {reason}")
        if (
            self.gates is not None
            and self.gates.enabled
            and self.gates.min_confirmations > 0
            and df is not None
        ):
            n, failed = self.count_confirmations(df, action)
            if n < self.gates.min_confirmations:
                return Signal.hold(
                    f"confirmation gate {n}/{self.gates.min_confirmations} "
                    f"(failed: {', '.join(failed)}): {reason}"
                )
        return Signal(
            action=action,
            stop_loss=stop_loss,
            target=target,
            confidence=confidence,
            reason=reason,
        )
