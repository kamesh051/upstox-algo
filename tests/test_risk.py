from datetime import date

from trading_system.config.settings import RiskConfig
from trading_system.risk import RiskManager
from trading_system.strategy import Action, Signal


def make_rm(**overrides) -> RiskManager:
    cfg = RiskConfig(
        capital_paise=50_000_000,  # Rs 5,00,000
        max_capital_per_trade_pct=0.10,
        max_concurrent_positions=3,
        daily_max_loss_pct=0.02,
        mandatory_stop_loss=True,
        **overrides,
    )
    return RiskManager(cfg)


def entry_signal(action=Action.BUY, stop_loss=1980.0, target=2050.0) -> Signal:
    return Signal(action=action, stop_loss=stop_loss, target=target, reason="test")


def test_position_sizing():
    rm = make_rm()
    # 10% of Rs 5,00,000 = Rs 50,000 per trade; price Rs 2000 -> 25 shares
    v = rm.evaluate_entry(entry_signal(), symbol="TEST", price=2000.0, open_positions=0)
    assert v.approved and v.qty == 25


def test_max_positions_veto():
    rm = make_rm()
    v = rm.evaluate_entry(entry_signal(), symbol="TEST", price=2000.0, open_positions=3)
    assert not v.approved and "max concurrent" in v.reason


def test_missing_stop_loss_veto():
    rm = make_rm()
    v = rm.evaluate_entry(Signal(action=Action.BUY), symbol="TEST", price=2000.0, open_positions=0)
    assert not v.approved and "stop_loss" in v.reason


def test_wrong_side_stop_loss_veto():
    rm = make_rm()
    v = rm.evaluate_entry(
        entry_signal(stop_loss=2100.0), symbol="TEST", price=2000.0, open_positions=0
    )
    assert not v.approved and "wrong side" in v.reason
    v = rm.evaluate_entry(
        entry_signal(action=Action.SELL, stop_loss=1900.0, target=None),
        symbol="TEST", price=2000.0,
        open_positions=0,
    )
    assert not v.approved and "wrong side" in v.reason


def test_daily_loss_halt_and_reset():
    rm = make_rm()
    rm.new_day(date(2026, 1, 5))
    # limit = 2% of 50,000,000 = 1,000,000 paise
    rm.record_realized_pnl(-999_999)
    assert not rm.halted
    rm.record_realized_pnl(-1)
    assert rm.halted
    v = rm.evaluate_entry(entry_signal(), symbol="TEST", price=2000.0, open_positions=0)
    assert not v.approved and "halted" in v.reason
    # next session clears the halt
    rm.new_day(date(2026, 1, 6))
    assert not rm.halted
    assert rm.evaluate_entry(entry_signal(), symbol="TEST", price=2000.0, open_positions=0).approved


def test_price_too_high_for_capital():
    rm = make_rm()
    # per-trade capital Rs 50,000; a Rs 60,000 stock cannot be sized
    v = rm.evaluate_entry(
        entry_signal(stop_loss=59_000.0), symbol="TEST", price=60_000.0, open_positions=0
    )
    assert not v.approved and v.qty == 0
