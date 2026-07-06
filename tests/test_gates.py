"""Trade-frequency gates (Phase 2.5 improvement #1)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading_system.backtest.engine import BacktestEngine
from trading_system.config.settings import GatesConfig, RiskConfig
from trading_system.risk import RiskManager
from trading_system.strategy import Action, Signal, Strategy
from tests.conftest import make_ohlc
from tests.test_backtest_integration import run_once


# -- confirmation gate -----------------------------------------------------------


class AlwaysBuy(Strategy):
    """Emits a BUY through the entry() gate on every candle (test double)."""

    warmup = 1

    def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
        if self.position is not None:
            return Signal.hold()
        last = df.iloc[-1]
        return self.entry(
            Action.BUY,
            stop_loss=last["close"] * 0.5,
            target=None,
            sentiment=sentiment,
            confidence=0.5,
            reason="always-buy",
            df=df,
        )


def confirm_frame(vwap_ok: bool, volume_ok: bool, macd_ok: bool) -> pd.DataFrame:
    """One-row frame where each confirmation check passes iff requested (long side)."""
    return pd.DataFrame(
        {
            "close": [100.0],
            "vwap": [99.0 if vwap_ok else 101.0],
            "volume": [20_000 if volume_ok else 5_000],
            "vol_avg20": [10_000.0],  # surge threshold at 1.5x = 15,000
            "macd_hist": [0.5 if macd_ok else -0.5],
        }
    )


@pytest.mark.parametrize(
    "vwap_ok,volume_ok,macd_ok,expect_entry",
    [
        (True, True, True, True),
        (True, True, False, True),   # 2 of 3 passes
        (True, False, False, False), # 1 of 3 fails the gate
        (False, False, False, False),
    ],
)
def test_confirmation_gate_thresholds(vwap_ok, volume_ok, macd_ok, expect_entry):
    strat = AlwaysBuy(gates=GatesConfig(min_confirmations=2))
    sig = strat.on_candle(confirm_frame(vwap_ok, volume_ok, macd_ok), sentiment=0.0)
    if expect_entry:
        assert sig.action == Action.BUY
    else:
        assert sig.action == Action.HOLD
        assert "confirmation gate" in sig.reason


def test_gate_reason_names_failed_checks():
    strat = AlwaysBuy(gates=GatesConfig(min_confirmations=2))
    sig = strat.on_candle(confirm_frame(True, False, False), sentiment=0.0)
    assert "volume" in sig.reason and "macd" in sig.reason
    assert "vwap" not in sig.reason


def test_nan_vol_avg_counts_as_failed():
    df = confirm_frame(True, True, False)
    df["vol_avg20"] = np.nan  # indicator warmup window
    strat = AlwaysBuy(gates=GatesConfig(min_confirmations=2))
    sig = strat.on_candle(df, sentiment=0.0)
    assert sig.action == Action.HOLD


def test_no_gates_means_no_gating():
    strat = AlwaysBuy(gates=None)
    sig = strat.on_candle(confirm_frame(False, False, False), sentiment=0.0)
    assert sig.action == Action.BUY


def test_short_side_checks_invert():
    strat = AlwaysBuy(gates=GatesConfig(min_confirmations=2))
    # long-failing frame (close below vwap, macd negative) confirms a SHORT
    n, failed = strat.count_confirmations(
        confirm_frame(False, False, False), Action.SELL
    )
    assert n == 2 and failed == ["volume"]


# -- daily caps (risk manager) ---------------------------------------------------


def capped_rm() -> RiskManager:
    rm = RiskManager(
        RiskConfig(
            capital_paise=50_000_000,
            gates=GatesConfig(max_trades_per_symbol_per_day=2, max_trades_per_day=3),
        )
    )
    rm.new_day(date(2026, 1, 5))
    return rm


def sig() -> Signal:
    return Signal(action=Action.BUY, stop_loss=90.0, reason="t")


def test_per_symbol_daily_cap():
    rm = capped_rm()
    for _ in range(2):
        assert rm.evaluate_entry(sig(), symbol="A", price=100.0, open_positions=0).approved
        rm.record_entry("A")
    v = rm.evaluate_entry(sig(), symbol="A", price=100.0, open_positions=0)
    assert not v.approved and "daily cap for A" in v.reason
    # a different symbol is still allowed
    assert rm.evaluate_entry(sig(), symbol="B", price=100.0, open_positions=0).approved


def test_total_daily_cap_and_reset():
    rm = capped_rm()
    for s in ("A", "A", "B"):
        rm.record_entry(s)
    v = rm.evaluate_entry(sig(), symbol="C", price=100.0, open_positions=0)
    assert not v.approved and "daily total cap" in v.reason
    rm.new_day(date(2026, 1, 6))
    assert rm.evaluate_entry(sig(), symbol="C", price=100.0, open_positions=0).approved


def test_caps_ignored_when_gates_disabled():
    rm = RiskManager(
        RiskConfig(capital_paise=50_000_000, gates=GatesConfig(enabled=False))
    )
    rm.new_day(date(2026, 1, 5))
    for _ in range(10):
        assert rm.evaluate_entry(sig(), symbol="A", price=100.0, open_positions=0).approved
        rm.record_entry("A")


# -- cooldown (engine) -------------------------------------------------------------


class ScalpBuy(Strategy):
    """Enters constantly with a near target: forces rapid exits to exercise cooldown."""

    warmup = 2

    def on_candle(self, df: pd.DataFrame, sentiment: float) -> Signal:
        if self.position is not None:
            return Signal.hold()
        last = df.iloc[-1]
        return Signal(
            action=Action.BUY,
            stop_loss=last["close"] * 0.90,
            target=last["close"] * 1.0005,
            reason="scalp",
        )


def cooldown_run(cooldown: int):
    gates = GatesConfig(
        min_confirmations=0,  # isolate the cooldown
        cooldown_candles=cooldown,
        max_trades_per_symbol_per_day=100,
        max_trades_per_day=100,
    )
    data = {"SYMA": make_ohlc(days=6, seed=5)}
    engine = BacktestEngine(
        strategy_cls=ScalpBuy,
        risk_manager=RiskManager(RiskConfig(capital_paise=50_000_000, gates=gates)),
        initial_capital_paise=50_000_000,
        gates=gates,
    )
    return engine.run(data, date(2026, 1, 5), date(2026, 1, 12))


def test_cooldown_spaces_reentries():
    trades = cooldown_run(cooldown=8).trades
    assert len(trades) >= 2
    for prev, nxt in zip(trades, trades[1:]):
        if prev.exit_time.date() == nxt.entry_time.date():
            # >= 8 candles x 15m between exit and the re-entry fill (signal
            # is blocked for 8 candles, fill comes on the candle after that)
            gap_minutes = (nxt.entry_time - prev.exit_time).total_seconds() / 60
            assert gap_minutes >= 8 * 15, (prev.exit_time, nxt.entry_time)


def test_no_cooldown_allows_immediate_reentry():
    n_with = len(cooldown_run(cooldown=8).trades)
    n_without = len(cooldown_run(cooldown=0).trades)
    assert n_without > n_with


# -- gated vs ungated end-to-end ---------------------------------------------------


def test_gates_reduce_trade_count():
    base = run_once()
    gated = run_once(gates=GatesConfig())
    assert 0 < len(gated.trades) < len(base.trades)


def test_gated_run_respects_daily_caps():
    gated = run_once(gates=GatesConfig())
    per_day_symbol: dict = {}
    per_day_total: dict = {}
    for t in gated.trades:
        d = t.entry_time.date()
        per_day_symbol[(d, t.symbol)] = per_day_symbol.get((d, t.symbol), 0) + 1
        per_day_total[d] = per_day_total.get(d, 0) + 1
    assert all(v <= 2 for v in per_day_symbol.values())
    assert all(v <= 6 for v in per_day_total.values())
