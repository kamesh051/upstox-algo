"""TradingEngine (paper mode) — scripted-tick session tests, no network."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_system.backtest.costs import order_costs
from trading_system.config.settings import RiskConfig
from trading_system.data.candles import IndicatorStream
from trading_system.engine import TradingEngine
from trading_system.execution import PaperBroker
from trading_system.risk import RiskManager
from trading_system.strategy import Action, Signal, Strategy
from tests.test_candles import tick

SYM, KEY = "RELIANCE", "NSE_EQ|INE002A01018"


class BuyOnce(Strategy):
    """Enters long on the first completed candle; SL 90%, target 120% of close."""

    warmup = 1

    def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
        if self.position is not None:
            return Signal.hold()
        close = df.iloc[-1]["close"]
        return Signal(
            action=Action.BUY, stop_loss=close * 0.9, target=close * 1.2, reason="test-entry"
        )


def make_engine(strategy_cls=BuyOnce, capital=50_000_000, **risk_overrides) -> TradingEngine:
    risk_cfg = RiskConfig(capital_paise=capital, **risk_overrides)
    return TradingEngine(
        strategy_cls=strategy_cls,
        risk=RiskManager(risk_cfg),
        broker=PaperBroker(slippage_pct=0.0),  # slippage 0 -> exact price asserts
        streams={SYM: IndicatorStream()},
        symbol_to_key={SYM: KEY},
        initial_capital_paise=capital,
        interval="1minute",
        gates=None,
        alert=lambda msg: None,
    )


def test_entry_fill_opens_position_with_sizing():
    engine = make_engine()
    engine.on_tick(tick(SYM, 9, 30, 10, 100.0))  # forming 09:30
    engine.on_tick(tick(SYM, 9, 31, 5, 100.0))   # completes 09:30 -> BUY order
    assert engine.broker.pending(SYM) is not None
    assert engine.open_positions == {}
    engine.on_tick(tick(SYM, 9, 31, 10, 100.0))  # next tick -> fill
    pos = engine.open_positions[SYM]
    # 10% of Rs 5,00,000 = Rs 50,000 at Rs 100 -> 500 shares
    assert pos.qty == 500
    assert pos.entry_price == 100.0
    assert pos.stop_loss == pytest.approx(90.0)


def test_stop_loss_exit_records_trade_with_costs():
    engine = make_engine()
    engine.on_tick(tick(SYM, 9, 30, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 5, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 10, 100.0))  # entry filled @100
    engine.on_tick(tick(SYM, 9, 31, 20, 89.0))   # SL breach -> exit order
    engine.on_tick(tick(SYM, 9, 31, 30, 89.0))   # exit filled @89
    assert engine.open_positions == {}
    assert len(engine.trades) == 1
    t = engine.trades[0]
    assert t.exit_reason == "stop-loss"
    gross = round((89.0 - 100.0) * 500 * 100)
    costs = (
        order_costs(round(100.0 * 500 * 100), is_buy=True).total
        + order_costs(round(89.0 * 500 * 100), is_buy=False).total
    )
    assert t.gross_pnl_paise == gross
    assert t.costs_paise == costs
    assert t.net_pnl_paise == gross - costs
    assert engine.realized_pnl_paise == t.net_pnl_paise


def test_target_exit():
    engine = make_engine()
    engine.on_tick(tick(SYM, 9, 30, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 5, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 20, 121.0))  # target 120 breached
    engine.on_tick(tick(SYM, 9, 31, 30, 121.0))
    assert engine.trades[0].exit_reason == "target"
    assert engine.trades[0].net_pnl_paise > 0


def test_square_off_at_1515_flattens():
    engine = make_engine()
    engine.on_tick(tick(SYM, 14, 20, 0, 100.0))
    engine.on_tick(tick(SYM, 14, 21, 0, 100.0))  # completes candle -> entry order
    engine.on_tick(tick(SYM, 14, 21, 30, 100.0))  # filled
    assert SYM in engine.open_positions
    engine.on_tick(tick(SYM, 15, 15, 1, 101.0))   # square-off trigger -> exit order
    engine.on_tick(tick(SYM, 15, 15, 5, 101.0))   # exit fill
    assert engine.open_positions == {}
    assert engine.trades[0].exit_reason == "intraday square-off"
    assert engine.day_report()["open_positions"] == 0


def test_no_new_entries_after_1430():
    engine = make_engine()
    engine.on_tick(tick(SYM, 14, 31, 0, 100.0))
    engine.on_tick(tick(SYM, 14, 32, 0, 100.0))  # candle completes at 14:31 >= 14:30
    assert engine.broker.pending(SYM) is None
    assert engine.open_positions == {}


def test_daily_loss_halt_blocks_reentry():
    # 0.1% daily loss cap on Rs 5,00,000 = Rs 500; the SL trade loses far more
    engine = make_engine(daily_max_loss_pct=0.001)
    engine.on_tick(tick(SYM, 9, 30, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 5, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 20, 89.0))
    engine.on_tick(tick(SYM, 9, 31, 30, 89.0))  # big loss -> halt
    assert engine.risk.halted
    engine.on_tick(tick(SYM, 9, 32, 0, 100.0))  # next candle -> BUY blocked by halt
    engine.on_tick(tick(SYM, 9, 33, 0, 100.0))
    assert engine.broker.pending(SYM) is None
    assert engine.rejected_entries >= 1
    assert engine.day_report()["halted"] is True


def test_strategy_receives_indicator_frame():
    seen: list[pd.DataFrame] = []

    class Recorder(Strategy):
        warmup = 1

        def on_candle(self, df, sentiment):
            seen.append(df)
            return Signal.hold()

    engine = make_engine(strategy_cls=Recorder)
    engine.on_tick(tick(SYM, 9, 30, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 5, 100.0))
    df = seen[0]
    for col in ("ema20", "rsi14", "vwap", "supertrend", "vol_avg20"):
        assert col in df.columns
    assert df.index[-1] == tick(SYM, 9, 30, 0, 0).ltt  # last row = completed candle


def test_day_report_reconciles():
    engine = make_engine()
    engine.on_tick(tick(SYM, 9, 30, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 5, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 10, 100.0))
    engine.on_tick(tick(SYM, 9, 31, 20, 121.0))
    engine.on_tick(tick(SYM, 9, 31, 30, 121.0))
    report = engine.day_report()
    assert report["trades"] == 1
    assert report["net_pnl_rupees"] == sum(t.net_pnl_paise for t in engine.trades) / 100
    assert report["open_positions"] == 0
