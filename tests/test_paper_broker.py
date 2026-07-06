import pytest

from trading_system.execution import Fill, OrderStatus, PaperBroker
from trading_system.strategy import Action
from tests.test_candles import tick


def test_fill_at_next_tick_with_buy_slippage():
    fills: list[Fill] = []
    broker = PaperBroker(slippage_pct=0.001, on_fill=fills.append)
    order = broker.place("RELIANCE", "KEY|RELIANCE", Action.BUY, 10, reason="test")
    assert order.status == OrderStatus.PENDING
    assert broker.on_tick(tick("RELIANCE", 10, 0, 0, 100.0)) is not None
    assert order.status == OrderStatus.FILLED
    assert fills[0].price == pytest.approx(100.1)  # buy fills worse (up)
    assert fills[0].qty == 10


def test_sell_slippage_is_downward():
    fills: list[Fill] = []
    broker = PaperBroker(slippage_pct=0.001, on_fill=fills.append)
    broker.place("TCS", "KEY|TCS", Action.SELL, 5)
    broker.on_tick(tick("TCS", 10, 0, 0, 200.0))
    assert fills[0].price == pytest.approx(199.8)  # sell fills worse (down)


def test_no_fill_without_tick_and_symbol_isolation():
    fills: list[Fill] = []
    broker = PaperBroker(on_fill=fills.append)
    broker.place("TCS", "KEY|TCS", Action.BUY, 5)
    assert broker.on_tick(tick("RELIANCE", 10, 0, 0, 100.0)) is None  # other symbol
    assert fills == []
    assert broker.pending("TCS") is not None


def test_cancel_all():
    broker = PaperBroker()
    o1 = broker.place("TCS", "K1", Action.BUY, 5)
    o2 = broker.place("INFY", "K2", Action.BUY, 5)
    assert broker.cancel_all("TCS") == 1
    assert o1.status == OrderStatus.CANCELLED
    assert broker.cancel_all() == 1  # remaining INFY order
    assert o2.status == OrderStatus.CANCELLED
    assert broker.on_tick(tick("TCS", 10, 0, 0, 100.0)) is None


def test_one_pending_order_per_symbol():
    broker = PaperBroker()
    broker.place("TCS", "K", Action.BUY, 5)
    with pytest.raises(RuntimeError):
        broker.place("TCS", "K", Action.SELL, 5)


def test_rejects_bad_orders():
    broker = PaperBroker()
    with pytest.raises(ValueError):
        broker.place("TCS", "K", Action.HOLD, 5)
    with pytest.raises(ValueError):
        broker.place("TCS", "K", Action.BUY, 0)
