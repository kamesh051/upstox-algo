"""Cost model vs a fully hand-computed round trip.

Example: buy 100 shares @ Rs 1000, sell 100 @ Rs 1010 (intraday).

BUY  (value 10,000,000 paise = Rs 100,000):
  brokerage    min(2000, 0.05% = 5000)        = 2000
  stt          0 (buy side)                   = 0
  exchange txn 0.00297% of 10,000,000 = 297   = 297
  sebi         0.0001%  of 10,000,000 = 10    = 10
  gst          18% of (2000+297+10)=415.26    = 415
  stamp        0.003%  of 10,000,000 = 300    = 300
  total                                       = 3022

SELL (value 10,100,000 paise = Rs 101,000):
  brokerage    min(2000, 5050)                = 2000
  stt          0.025% of 10,100,000 = 2525    = 2525
  exchange txn 0.00297% -> 299.97             = 300
  sebi         0.0001%  -> 10.1               = 10
  gst          18% of 2310 = 415.8            = 416
  stamp        0 (sell side)                  = 0
  total                                       = 5251

Round trip = 8273 paise = Rs 82.73
"""

import pytest

from trading_system.backtest.costs import order_costs, round_trip_costs


def test_buy_side_hand_computed():
    c = order_costs(10_000_000, is_buy=True)
    assert c.brokerage == 2000
    assert c.stt == 0
    assert c.exchange_txn == 297
    assert c.sebi == 10
    assert c.gst == 415
    assert c.stamp == 300
    assert c.total == 3022


def test_sell_side_hand_computed():
    c = order_costs(10_100_000, is_buy=False)
    assert c.brokerage == 2000
    assert c.stt == 2525
    assert c.exchange_txn == 300
    assert c.sebi == 10
    assert c.gst == 416
    assert c.stamp == 0
    assert c.total == 5251


def test_round_trip():
    assert round_trip_costs(10_000_000, 10_100_000) == 8273


def test_brokerage_caps_at_percentage_for_small_orders():
    # Rs 100 order -> 0.05% = 5 paise beats the Rs 20 flat fee
    c = order_costs(10_000, is_buy=True)
    assert c.brokerage == 5


def test_negative_value_rejected():
    with pytest.raises(ValueError):
        order_costs(-1, is_buy=True)
