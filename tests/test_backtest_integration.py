"""End-to-end backtest on a deterministic synthetic dataset.

Asserts the run is reproducible (identical metrics on identical input) and
that engine invariants hold: intraday-only trades, square-off honored, equity
reconciles with trade P&L.
"""

from datetime import date, time, timedelta

import pytest

from trading_system.backtest.engine import BacktestEngine, BacktestResult
from trading_system.backtest.metrics import compute_metrics
from trading_system.backtest.walkforward import walk_forward_splits
from trading_system.config.settings import GatesConfig, RiskConfig
from trading_system.risk import RiskManager
from trading_system.strategy import STRATEGIES, SupertrendFollow
from tests.conftest import make_ohlc

FROM, TO = date(2026, 1, 5), date(2026, 3, 6)


def run_once(strategy_cls=SupertrendFollow, gates: GatesConfig | None = None) -> BacktestResult:
    """Baseline harness runs ungated (Phase 2 behavior) unless gates are given."""
    gates = gates or GatesConfig(enabled=False)
    data = {
        "SYMA": make_ohlc(days=45, seed=21),
        "SYMB": make_ohlc(days=45, seed=42, start_price=500.0),
    }
    engine = BacktestEngine(
        strategy_cls=strategy_cls,
        risk_manager=RiskManager(
            RiskConfig(capital_paise=50_000_000, gates=gates)
        ),
        initial_capital_paise=50_000_000,
        slippage_pct=0.0003,
        gates=gates,
    )
    return engine.run(data, FROM, TO)


@pytest.fixture(scope="module")
def result() -> BacktestResult:
    return run_once()


def test_produces_trades(result):
    assert len(result.trades) > 0
    assert not result.equity.empty


def test_metrics_reproducible(result):
    again = run_once()
    m1, m2 = compute_metrics(result), compute_metrics(again)
    assert m1 == m2


def test_all_trades_intraday_and_squared_off(result):
    for t in result.trades:
        assert t.entry_time.date() == t.exit_time.date(), t
        assert t.exit_time.time() <= time(15, 15), t
        # same-candle exits are legitimate: entry at open, SL/target within candle
        assert t.entry_time <= t.exit_time, t


def test_equity_reconciles_with_trades(result):
    net = sum(t.net_pnl_paise for t in result.trades)
    assert result.equity.iloc[-1] == result.initial_capital_paise + net


def test_costs_always_positive(result):
    for t in result.trades:
        assert t.costs_paise > 0
        assert t.net_pnl_paise == t.gross_pnl_paise - t.costs_paise


def test_every_strategy_runs_clean():
    for name in STRATEGIES:
        res = run_once(STRATEGIES[name])
        m = compute_metrics(res)
        assert m["total_trades"] == len(res.trades)


def test_walk_forward_splits():
    splits = walk_forward_splits(
        date(2026, 1, 1), date(2026, 6, 30), train_days=90, validate_days=30
    )
    assert len(splits) == 3
    first = splits[0]
    assert first.train_from == date(2026, 1, 1)
    assert first.train_to == date(2026, 3, 31)
    assert first.validate_from == date(2026, 4, 1)
    assert first.validate_to == date(2026, 4, 30)
    # windows step forward by exactly validate_days; validate ranges are contiguous
    for a, b in zip(splits, splits[1:]):
        assert b.validate_from == a.validate_from + timedelta(days=30)
        assert b.validate_from == a.validate_to + timedelta(days=1)
    with pytest.raises(ValueError):
        walk_forward_splits(date(2026, 2, 1), date(2026, 1, 1), 10, 5)
