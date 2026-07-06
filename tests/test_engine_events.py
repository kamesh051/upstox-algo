"""TradingEngine event publishing: the dashboard's data source must be complete."""

import pandas as pd

from trading_system.config.settings import RiskConfig
from trading_system.data.candles import IndicatorStream
from trading_system.engine import TradingEngine
from trading_system.execution import PaperBroker
from trading_system.risk import RiskManager
from trading_system.strategy import Action, Signal, Strategy
from tests.test_candles import tick
from tests.test_trading_engine import SYM, KEY, BuyOnce


class RecordingBus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def of(self, type_):
        return [e for e in self.events if e.type == type_]


def run_session(strategy_cls=BuyOnce, **risk_overrides):
    bus = RecordingBus()
    engine = TradingEngine(
        strategy_cls=strategy_cls,
        risk=RiskManager(RiskConfig(capital_paise=50_000_000, **risk_overrides)),
        broker=PaperBroker(slippage_pct=0.0),
        streams={SYM: IndicatorStream()},
        symbol_to_key={SYM: KEY},
        initial_capital_paise=50_000_000,
        interval="1minute",
        gates=None,
        bus=bus,
    )
    engine.on_tick(tick(SYM, 9, 30, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 5, 100.0))   # candle completes -> BUY
    engine.on_tick(tick(SYM, 9, 31, 10, 100.0))  # entry fill
    engine.on_tick(tick(SYM, 9, 31, 20, 89.0))   # SL breach -> exit order
    engine.on_tick(tick(SYM, 9, 31, 30, 89.0))   # exit fill
    return engine, bus


def test_full_lifecycle_event_sequence():
    engine, bus = run_session()
    assert len(bus.of("tick")) == 5
    assert len(bus.of("candle")) == 1  # 09:30 completed (09:31 still forming)

    signals = bus.of("signal")
    assert signals and signals[0].payload["action"] == "BUY"
    assert signals[0].payload["reason"] == "test-entry"

    orders = bus.of("order")
    assert [o.payload["side"] for o in orders] == ["BUY", "SELL"]
    assert orders[1].payload["reason"] == "stop-loss"

    positions = bus.of("position")
    assert [p.payload["status"] for p in positions] == ["open", "closed"]
    assert positions[1].payload["net_pnl_paise"] == engine.trades[0].net_pnl_paise

    risk_events = bus.of("risk")
    day = [e for e in risk_events if e.payload["kind"] == "day_pnl"]
    assert day and day[-1].payload["daily_pnl_paise"] == engine.trades[0].net_pnl_paise


def test_hold_signals_published_with_reason():
    class AlwaysHold(Strategy):
        warmup = 1

        def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
            return Signal.hold("nothing aligned")

    _, bus = run_session(strategy_cls=AlwaysHold)
    signals = bus.of("signal")
    assert signals, "HOLD evaluations must be published (the 'why' screen)"
    assert signals[0].payload["action"] == "HOLD"
    assert signals[0].payload["reason"] == "nothing aligned"


def test_rejection_published_as_risk_event():
    class NoStopBuy(Strategy):
        warmup = 1

        def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
            return Signal(action=Action.BUY, reason="reckless")  # no stop_loss

    _, bus = run_session(strategy_cls=NoStopBuy)
    rejections = [e for e in bus.of("risk") if e.payload["kind"] == "entry_rejected"]
    assert rejections and "stop_loss" in rejections[0].payload["reason"]
