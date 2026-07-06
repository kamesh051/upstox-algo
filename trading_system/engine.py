"""Live trading engine — shared by paper (now) and real execution (Phase 6).

The live twin of ``backtest.engine.BacktestEngine``: identical strategy
interface, the same RiskManager and frequency gates, the same cost model.
The only moving part is the broker (PaperBroker today, real order manager in
Phase 6) — design rule 6.

Per tick:
1. broker fills pending orders (market orders fill at the next tick)
2. session square-off at ``square_off_time`` flattens everything
3. stop-loss/target checks on the tick's ltp (tick-level, finer than the
   backtester's candle high/low)
4. completed signal-interval candles -> ``strategy.on_candle(frame, sentiment)``
   -> risk veto -> entry/exit orders

Money: prices float (market data), P&L/costs in paise ints via the shared
cost model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time

from trading_system.backtest.costs import order_costs
from trading_system.backtest.engine import Trade
from trading_system.config.settings import GatesConfig
from trading_system.data.candles import CandleBuilder, IndicatorStream
from trading_system.data.live_feed import Tick
from trading_system.events import EventBus, NullBus, make_event
from trading_system.execution.paper_broker import Fill, PaperBroker
from trading_system.logging_setup import get_logger
from trading_system.risk.manager import RiskManager
from trading_system.strategy.base import Action, PositionState, Side, Signal, Strategy

log = get_logger(__name__)

SentimentProvider = Callable[[str, datetime], float]
AlertFn = Callable[[str], None]


def neutral_sentiment(symbol: str, ts: datetime) -> float:
    return 0.0


@dataclass
class _OpenPosition:
    state: PositionState
    entry_costs_paise: int
    entry_reason: str
    exit_pending: bool = False


@dataclass
class _PendingEntry:
    signal: Signal
    qty: int


@dataclass
class TradingEngine:
    strategy_cls: type[Strategy]
    risk: RiskManager
    broker: PaperBroker
    streams: dict[str, IndicatorStream]  # symbol -> seeded indicator stream
    symbol_to_key: dict[str, str]
    initial_capital_paise: int
    interval: str = "15minute"
    square_off_time: time = time(15, 15)
    no_new_entries_after: time = time(14, 30)
    gates: GatesConfig | None = None
    sentiment_provider: SentimentProvider = neutral_sentiment
    alert: AlertFn = lambda msg: None
    bus: EventBus = field(default_factory=NullBus)

    trades: list[Trade] = field(default_factory=list)
    rejected_entries: int = 0

    def __post_init__(self) -> None:
        self.gates = self.gates if self.gates is not None and self.gates.enabled else None
        # drop_partial_first: a mid-bucket session start must not emit a
        # partial candle (produced a bogus Supertrend signal on 2026-07-03)
        self.builder = CandleBuilder([self.interval], drop_partial_first=True)
        self._strategies = {
            sym: self.strategy_cls(gates=self.gates) for sym in self.streams
        }
        self._positions: dict[str, _OpenPosition] = {}
        self._pending_entries: dict[str, _PendingEntry] = {}
        self._candle_count: dict[str, int] = {sym: 0 for sym in self.streams}
        self._cooldown_until: dict[str, int] = {}
        self._squared_off = False
        self._last_tick: Tick | None = None
        self.last_tick_by_symbol: dict[str, Tick] = {}
        self.broker.on_fill = self._on_fill
        self.realized_pnl_paise = 0

    # -- public surface -----------------------------------------------------

    def on_tick(self, tick: Tick) -> None:
        self._last_tick = tick
        self.last_tick_by_symbol[tick.symbol] = tick
        self.bus.publish(make_event(
            "tick",
            {"symbol": tick.symbol, "ltp": tick.ltp, "ltt": tick.ltt.isoformat()},
        ))
        self.risk.new_day(tick.ltt.date())
        self.broker.on_tick(tick)  # fills route through _on_fill

        if tick.ltt.time() >= self.square_off_time:
            self._square_off_all()
        else:
            self._squared_off = False  # new session
            self._check_stops(tick)

        for candle in self.builder.on_tick(tick):
            self._on_candle_complete(candle)

    async def run(self, feed) -> None:
        async for tick in feed.ticks():
            self.on_tick(tick)

    @property
    def open_positions(self) -> dict[str, PositionState]:
        return {sym: op.state for sym, op in self._positions.items()}

    def square_off_all(self, reason: str = "square-off") -> None:
        """Failsafe entry point (scheduler); also triggered tick-side at 15:15."""
        # cancel un-filled ENTRY orders first (positions' exit orders must survive)
        for sym in list(self._pending_entries):
            self.broker.cancel_all(sym)
        self._pending_entries.clear()
        for sym in list(self._positions):
            self._request_exit(sym, reason)

    def day_report(self) -> dict:
        wins = [t for t in self.trades if t.net_pnl_paise > 0]
        return {
            "date": (self._last_tick.ltt.date().isoformat() if self._last_tick else ""),
            "strategy": self.strategy_cls.__name__,
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(self.trades) - len(wins),
            "net_pnl_rupees": self.realized_pnl_paise / 100,
            "costs_rupees": sum(t.costs_paise for t in self.trades) / 100,
            "rejected_entries": self.rejected_entries,
            "halted": self.risk.halted,
            "open_positions": len(self._positions),  # should be 0 after square-off
        }

    # -- internals ----------------------------------------------------------

    def _square_off_all(self) -> None:
        if self._squared_off:
            return
        self._squared_off = True
        if self._positions:
            self.alert(f"15:15 square-off: closing {len(self._positions)} position(s)")
        self.square_off_all(reason="intraday square-off")

    def _check_stops(self, tick: Tick) -> None:
        op = self._positions.get(tick.symbol)
        if op is None or op.exit_pending:
            return
        pos = op.state
        if pos.side == Side.LONG:
            if tick.ltp <= pos.stop_loss:
                self._request_exit(tick.symbol, "stop-loss")
            elif pos.target is not None and tick.ltp >= pos.target:
                self._request_exit(tick.symbol, "target")
        else:
            if tick.ltp >= pos.stop_loss:
                self._request_exit(tick.symbol, "stop-loss")
            elif pos.target is not None and tick.ltp <= pos.target:
                self._request_exit(tick.symbol, "target")

    def _request_exit(self, symbol: str, reason: str) -> None:
        op = self._positions.get(symbol)
        if op is None or op.exit_pending:
            return
        op.exit_pending = True
        side = Action.SELL if op.state.side == Side.LONG else Action.BUY
        order = self.broker.place(
            symbol, self.symbol_to_key[symbol], side, op.state.qty, reason=reason
        )
        self.bus.publish(make_event(
            "order",
            {
                "order_id": order.id, "symbol": symbol, "side": side.value,
                "qty": order.qty, "status": order.status.value, "reason": reason,
            },
        ))

    def _on_candle_complete(self, candle) -> None:
        sym = candle.symbol
        self._candle_count[sym] += 1
        stream = self.streams[sym]
        stream.append(candle)
        self.bus.publish(make_event(
            "candle",
            {
                "symbol": sym, "interval": candle.interval, "ts": candle.ts.isoformat(),
                "open": candle.open, "high": candle.high, "low": candle.low,
                "close": candle.close, "volume": candle.volume,
            },
        ))

        if candle.ts.time() >= self.square_off_time:
            return
        strat = self._strategies[sym]
        if len(stream) < strat.warmup:  # same warm-up rule as the backtester
            return
        strat.position = self._positions[sym].state if sym in self._positions else None
        sentiment = self.sentiment_provider(sym, candle.ts)
        signal = strat.on_candle(stream.frame, sentiment)
        # every evaluation is published, HOLDs included — the "why" screen
        # renders from these; gate/veto reasons ride in the reason string
        self.bus.publish(make_event(
            "signal",
            {
                "symbol": sym, "candle_ts": candle.ts.isoformat(),
                "action": signal.action.value, "reason": signal.reason,
                "stop_loss": signal.stop_loss, "target": signal.target,
                "confidence": signal.confidence, "in_position": strat.position is not None,
            },
        ))

        if signal.action == Action.EXIT and sym in self._positions:
            self._request_exit(sym, signal.reason or "strategy exit")
            return
        if signal.action not in (Action.BUY, Action.SELL):
            return
        if (
            sym in self._positions
            or sym in self._pending_entries
            or candle.ts.time() >= self.no_new_entries_after
        ):
            return
        if self.gates and self._candle_count[sym] < self._cooldown_until.get(sym, 0):
            self._reject_entry(sym, "cooldown")
            return
        verdict = self.risk.evaluate_entry(
            signal, symbol=sym, price=candle.close,
            open_positions=len(self._positions) + len(self._pending_entries),
        )
        if not verdict.approved:
            self._reject_entry(sym, verdict.reason)
            return
        self._pending_entries[sym] = _PendingEntry(signal, verdict.qty)
        self.risk.record_entry(sym)
        order = self.broker.place(
            sym, self.symbol_to_key[sym], signal.action, verdict.qty,
            reason=signal.reason,
        )
        self.bus.publish(make_event(
            "order",
            {
                "order_id": order.id, "symbol": sym, "side": order.side.value,
                "qty": order.qty, "status": order.status.value, "reason": order.reason,
            },
        ))

    def _reject_entry(self, sym: str, reason: str) -> None:
        self.rejected_entries += 1
        log.info("engine.entry_rejected", symbol=sym, reason=reason)
        self.bus.publish(make_event(
            "risk", {"kind": "entry_rejected", "symbol": sym, "reason": reason},
        ))

    def _on_fill(self, fill: Fill) -> None:
        if fill.symbol in self._positions:
            self._close_position(fill)
        else:
            self._open_position(fill)

    def _open_position(self, fill: Fill) -> None:
        intent = self._pending_entries.pop(fill.symbol, None)
        if intent is None:
            log.warning("engine.orphan_fill", symbol=fill.symbol, order_id=fill.order_id)
            return
        side = Side.LONG if fill.side == Action.BUY else Side.SHORT
        value_paise = round(fill.price * fill.qty * 100)
        costs = order_costs(value_paise, is_buy=side == Side.LONG).total
        self._positions[fill.symbol] = _OpenPosition(
            state=PositionState(
                symbol=fill.symbol,
                side=side,
                qty=fill.qty,
                entry_price=fill.price,
                stop_loss=intent.signal.stop_loss,
                target=intent.signal.target,
                entry_time=fill.ts,
            ),
            entry_costs_paise=costs,
            entry_reason=intent.signal.reason,
        )
        self.alert(
            f"ENTRY {side.value} {fill.symbol} x{fill.qty} @ {fill.price:.2f} "
            f"SL {intent.signal.stop_loss:.2f} ({intent.signal.reason})"
        )
        self.bus.publish(make_event(
            "position",
            {
                "status": "open", "symbol": fill.symbol, "side": side.value,
                "qty": fill.qty, "entry_price": fill.price,
                "stop_loss": intent.signal.stop_loss, "target": intent.signal.target,
                "entry_time": fill.ts.isoformat(), "entry_costs_paise": costs,
                "open_positions": len(self._positions),
            },
        ))

    def _close_position(self, fill: Fill) -> None:
        op = self._positions.pop(fill.symbol)
        pos = op.state
        exit_value_paise = round(fill.price * fill.qty * 100)
        exit_costs = order_costs(exit_value_paise, is_buy=pos.side == Side.SHORT).total
        sign = 1 if pos.side == Side.LONG else -1
        gross = round(sign * (fill.price - pos.entry_price) * pos.qty * 100)
        costs = op.entry_costs_paise + exit_costs
        net = gross - costs
        self.realized_pnl_paise += net
        self.trades.append(
            Trade(
                symbol=fill.symbol,
                side=pos.side,
                qty=pos.qty,
                entry_time=pos.entry_time,
                entry_price=round(pos.entry_price, 4),
                exit_time=fill.ts,
                exit_price=round(fill.price, 4),
                gross_pnl_paise=gross,
                costs_paise=costs,
                net_pnl_paise=net,
                entry_reason=op.entry_reason,
                exit_reason=fill.reason,
            )
        )
        self.risk.record_realized_pnl(net)
        if self.gates:
            self._cooldown_until[fill.symbol] = (
                self._candle_count[fill.symbol] + self.gates.cooldown_candles
            )
        self.alert(
            f"EXIT {pos.side.value} {fill.symbol} x{pos.qty} @ {fill.price:.2f} "
            f"net Rs {net / 100:,.2f} ({fill.reason}) | day P&L Rs {self.realized_pnl_paise / 100:,.2f}"
        )
        self.bus.publish(make_event(
            "position",
            {
                "status": "closed", "symbol": fill.symbol, "side": pos.side.value,
                "qty": pos.qty, "exit_price": fill.price, "exit_reason": fill.reason,
                "net_pnl_paise": net, "costs_paise": costs,
                "open_positions": len(self._positions),
            },
        ))
        self.bus.publish(make_event(
            "risk",
            {
                "kind": "day_pnl", "daily_pnl_paise": self.risk.daily_realized_pnl_paise,
                "halted": self.risk.halted, "open_positions": len(self._positions),
                "trades_today": len(self.trades),
            },
        ))
