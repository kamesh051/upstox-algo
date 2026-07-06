"""Event-driven, candle-by-candle backtest engine.

Execution semantics (chosen to make lookahead impossible):
- A strategy sees candles up to and including the one that just CLOSED
  (``df.iloc[:i+1]``) and emits a Signal.
- Entry/exit signals become pending orders, filled at the NEXT candle's open,
  adjusted for slippage (buy worse-up, sell worse-down).
- While a position is open, each new candle first checks stop-loss/target
  against its high/low. If both could have been hit in the same candle, the
  STOP fills first (conservative assumption).
- Candle timestamps are interval-start: the 15:15 candle spans 15:15-15:30.
  At ``square_off_time`` (default 15:15) any open position is closed at that
  candle's open — the mandated intraday square-off. New entries stop earlier
  (``no_new_entries_after``).
- One strategy instance per symbol (strategies are stateful via
  ``self.position``); risk manager is shared portfolio-wide.

Money: prices are rupees (float); every P&L/cost number is paise (int).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Callable

import pandas as pd

from trading_system.backtest.costs import order_costs
from trading_system.config.settings import GatesConfig
from trading_system.data.indicators import add_indicators
from trading_system.logging_setup import get_logger
from trading_system.risk.manager import RiskManager
from trading_system.strategy.base import (
    Action,
    PositionState,
    Side,
    Signal,
    Strategy,
)

log = get_logger(__name__)

SentimentProvider = Callable[[str, datetime], float]


def neutral_sentiment(symbol: str, ts: datetime) -> float:
    return 0.0


@dataclass
class Trade:
    symbol: str
    side: Side
    qty: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    gross_pnl_paise: int
    costs_paise: int
    net_pnl_paise: int
    entry_reason: str
    exit_reason: str


@dataclass
class _PendingOrder:
    action: Action  # BUY/SELL for entries, EXIT to close
    signal: Signal
    qty: int = 0  # sized at approval time (entries only)


@dataclass
class BacktestResult:
    strategy: str
    from_date: date
    to_date: date
    initial_capital_paise: int
    trades: list[Trade]
    equity: pd.Series  # paise, indexed by candle timestamp
    rejected_entries: int = 0

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame([t.__dict__ for t in self.trades])


@dataclass
class _OpenPosition:
    state: PositionState
    entry_costs_paise: int
    entry_reason: str


class BacktestEngine:
    def __init__(
        self,
        strategy_cls: type[Strategy],
        risk_manager: RiskManager,
        initial_capital_paise: int,
        slippage_pct: float = 0.0003,
        square_off_time: time = time(15, 15),
        no_new_entries_after: time = time(14, 30),
        sentiment_provider: SentimentProvider = neutral_sentiment,
        gates: GatesConfig | None = None,
    ):
        self.strategy_cls = strategy_cls
        self.risk = risk_manager
        self.initial_capital_paise = initial_capital_paise
        self.slippage_pct = slippage_pct
        self.square_off_time = square_off_time
        self.no_new_entries_after = no_new_entries_after
        self.sentiment_provider = sentiment_provider
        # trade-frequency gates; None or enabled=False reproduces ungated behavior
        self.gates = gates if gates is not None and gates.enabled else None

    # -- fills -------------------------------------------------------------

    def _fill_price(self, price: float, is_buy: bool) -> float:
        adj = 1 + self.slippage_pct if is_buy else 1 - self.slippage_pct
        return price * adj

    # -- main loop -----------------------------------------------------------

    def run(
        self,
        data: dict[str, pd.DataFrame],
        from_date: date,
        to_date: date,
    ) -> BacktestResult:
        """data: symbol -> OHLCV DataFrame (may include pre-from_date warmup rows)."""
        frames = {sym: add_indicators(df) for sym, df in data.items()}
        strategies = {sym: self.strategy_cls(gates=self.gates) for sym in frames}
        row_pos = {
            sym: {ts: i for i, ts in enumerate(df.index)} for sym, df in frames.items()
        }

        timeline = sorted(set().union(*[df.index for df in frames.values()]))
        positions: dict[str, _OpenPosition] = {}
        pending: dict[str, _PendingOrder] = {}
        cooldown_until: dict[str, int] = {}  # symbol -> first row index allowed back in
        trades: list[Trade] = []
        equity_points: list[tuple[datetime, int]] = []
        realized_pnl_paise = 0
        rejected = 0

        for ts in timeline:
            self.risk.new_day(ts.date())

            for sym, df in frames.items():
                i = row_pos[sym].get(ts)
                if i is None:
                    continue
                candle = df.iloc[i]
                strat = strategies[sym]

                # 1. fill last candle's pending order at this candle's open
                order = pending.pop(sym, None)
                if order is not None:
                    if order.action in (Action.BUY, Action.SELL):
                        if ts.time() < self.square_off_time and sym not in positions:
                            positions[sym] = self._open_position(
                                sym, order, candle, ts
                            )
                    elif sym in positions:
                        realized_pnl_paise += self._close_position(
                            positions, trades, sym,
                            price=self._fill_price(
                                candle["open"],
                                is_buy=positions[sym].state.side == Side.SHORT,
                            ),
                            ts=ts, reason=order.signal.reason or "strategy exit",
                        )
                        if self.gates:
                            cooldown_until[sym] = i + self.gates.cooldown_candles

                # 2. manage the open position on this candle
                if sym in positions:
                    pos = positions[sym].state
                    exit_px, exit_reason = None, ""
                    if ts.time() >= self.square_off_time:
                        exit_px = candle["open"]
                        exit_reason = "intraday square-off"
                    elif pos.side == Side.LONG and candle["low"] <= pos.stop_loss:
                        exit_px, exit_reason = pos.stop_loss, "stop-loss"
                    elif pos.side == Side.SHORT and candle["high"] >= pos.stop_loss:
                        exit_px, exit_reason = pos.stop_loss, "stop-loss"
                    elif (
                        pos.target is not None
                        and (
                            (pos.side == Side.LONG and candle["high"] >= pos.target)
                            or (pos.side == Side.SHORT and candle["low"] <= pos.target)
                        )
                    ):
                        exit_px, exit_reason = pos.target, "target"
                    if exit_px is not None:
                        realized_pnl_paise += self._close_position(
                            positions, trades, sym,
                            price=self._fill_price(
                                exit_px, is_buy=pos.side == Side.SHORT
                            ),
                            ts=ts, reason=exit_reason,
                        )
                        if self.gates:
                            cooldown_until[sym] = i + self.gates.cooldown_candles

                # 3. strategy signal on the completed candle
                if i + 1 < strat.warmup or ts.date() < from_date or ts.date() > to_date:
                    continue
                if ts.time() >= self.square_off_time:
                    continue  # session over for signal purposes
                strat.position = positions[sym].state if sym in positions else None
                sentiment = self.sentiment_provider(sym, ts)
                signal = strat.on_candle(df.iloc[: i + 1], sentiment)

                if signal.action == Action.EXIT and sym in positions:
                    pending[sym] = _PendingOrder(Action.EXIT, signal)
                elif signal.action in (Action.BUY, Action.SELL):
                    if sym in positions or ts.time() >= self.no_new_entries_after:
                        continue
                    if self.gates and i < cooldown_until.get(sym, -1):
                        rejected += 1
                        log.debug("risk.rejected", symbol=sym, reason="cooldown")
                        continue
                    verdict = self.risk.evaluate_entry(
                        signal, symbol=sym, price=candle["close"],
                        open_positions=len(positions),
                    )
                    if verdict.approved:
                        pending[sym] = _PendingOrder(signal.action, signal, verdict.qty)
                        self.risk.record_entry(sym)
                    else:
                        rejected += 1
                        log.debug("risk.rejected", symbol=sym, reason=verdict.reason)

            # portfolio equity mark-to-market at this timestamp's closes
            unrealized = 0
            for sym, op in positions.items():
                j = row_pos[sym].get(ts)
                px = frames[sym].iloc[j]["close"] if j is not None else op.state.entry_price
                sign = 1 if op.state.side == Side.LONG else -1
                unrealized += round(sign * (px - op.state.entry_price) * op.state.qty * 100)
            equity_points.append(
                (ts, self.initial_capital_paise + realized_pnl_paise + unrealized)
            )

        # close anything still open at the end of data
        for sym in list(positions):
            last = frames[sym].iloc[-1]
            realized_pnl_paise += self._close_position(
                positions, trades, sym,
                price=self._fill_price(
                    last["close"], is_buy=positions[sym].state.side == Side.SHORT
                ),
                ts=frames[sym].index[-1], reason="end of data",
            )

        equity = pd.Series(
            dict(equity_points), name="equity_paise"
        ).sort_index()
        log.info(
            "backtest.done",
            strategy=self.strategy_cls.__name__,
            trades=len(trades),
            net_pnl_paise=realized_pnl_paise,
            rejected_entries=rejected,
        )
        return BacktestResult(
            strategy=self.strategy_cls.__name__,
            from_date=from_date,
            to_date=to_date,
            initial_capital_paise=self.initial_capital_paise,
            trades=trades,
            equity=equity,
            rejected_entries=rejected,
        )

    # -- position lifecycle ----------------------------------------------------

    def _open_position(
        self, sym: str, order: _PendingOrder, candle: pd.Series, ts: datetime
    ) -> _OpenPosition:
        side = Side.LONG if order.action == Action.BUY else Side.SHORT
        fill = self._fill_price(candle["open"], is_buy=side == Side.LONG)
        value_paise = round(fill * order.qty * 100)
        costs = order_costs(value_paise, is_buy=side == Side.LONG).total
        return _OpenPosition(
            state=PositionState(
                symbol=sym,
                side=side,
                qty=order.qty,
                entry_price=fill,
                stop_loss=order.signal.stop_loss,
                target=order.signal.target,
                entry_time=ts,
            ),
            entry_costs_paise=costs,
            entry_reason=order.signal.reason,
        )

    def _close_position(
        self,
        positions: dict[str, _OpenPosition],
        trades: list[Trade],
        sym: str,
        price: float,
        ts: datetime,
        reason: str,
    ) -> int:
        op = positions.pop(sym)
        pos = op.state
        exit_value_paise = round(price * pos.qty * 100)
        exit_costs = order_costs(
            exit_value_paise, is_buy=pos.side == Side.SHORT
        ).total
        sign = 1 if pos.side == Side.LONG else -1
        gross = round(sign * (price - pos.entry_price) * pos.qty * 100)
        costs = op.entry_costs_paise + exit_costs
        net = gross - costs
        trades.append(
            Trade(
                symbol=sym,
                side=pos.side,
                qty=pos.qty,
                entry_time=pos.entry_time,
                entry_price=round(pos.entry_price, 4),
                exit_time=ts,
                exit_price=round(price, 4),
                gross_pnl_paise=gross,
                costs_paise=costs,
                net_pnl_paise=net,
                entry_reason=op.entry_reason,
                exit_reason=reason,
            )
        )
        self.risk.record_realized_pnl(net)
        return net
