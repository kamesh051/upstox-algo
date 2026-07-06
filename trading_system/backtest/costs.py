"""Indian intraday equity cost model. All amounts in paise (int).

Per executed order, on the order value:
- brokerage: min(Rs 20, 0.05%)                          both sides
- STT: 0.025%                                           sell side only (intraday)
- exchange transaction charges (NSE): 0.00297%          both sides
- SEBI turnover fee: 0.0001%  (Rs 10 / crore)           both sides
- GST: 18% on (brokerage + exchange txn + SEBI)         both sides
- stamp duty: 0.003%                                    buy side only

Slippage is NOT a fee here — engines model it as a worse fill price
(buy at px*(1+s), sell at px*(1-s)).

Each component is rounded to the nearest paisa independently; the hand-computed
example in tests/test_costs.py pins these numbers down.
"""

from __future__ import annotations

from dataclasses import dataclass

BROKERAGE_FLAT_PAISE = 2000  # Rs 20
BROKERAGE_PCT = 0.0005  # 0.05%
STT_SELL_PCT = 0.00025  # 0.025%
EXCHANGE_TXN_PCT = 0.0000297  # 0.00297%
SEBI_PCT = 0.000001  # 0.0001%
GST_PCT = 0.18
STAMP_BUY_PCT = 0.00003  # 0.003%


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: int
    stt: int
    exchange_txn: int
    sebi: int
    gst: int
    stamp: int

    @property
    def total(self) -> int:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi + self.gst + self.stamp


def order_costs(value_paise: int, is_buy: bool) -> CostBreakdown:
    """Costs for one executed order of the given traded value."""
    if value_paise < 0:
        raise ValueError("order value must be non-negative")
    brokerage = min(BROKERAGE_FLAT_PAISE, round(value_paise * BROKERAGE_PCT))
    stt = 0 if is_buy else round(value_paise * STT_SELL_PCT)
    exchange_txn = round(value_paise * EXCHANGE_TXN_PCT)
    sebi = round(value_paise * SEBI_PCT)
    gst = round((brokerage + exchange_txn + sebi) * GST_PCT)
    stamp = round(value_paise * STAMP_BUY_PCT) if is_buy else 0
    return CostBreakdown(brokerage, stt, exchange_txn, sebi, gst, stamp)


def round_trip_costs(buy_value_paise: int, sell_value_paise: int) -> int:
    """Total charges for one intraday round trip (one buy + one sell order)."""
    return (
        order_costs(buy_value_paise, is_buy=True).total
        + order_costs(sell_value_paise, is_buy=False).total
    )
