"""Simulated broker: market orders fill at the NEXT tick with slippage.

This is the paper-mode stand-in for the real order manager (Phase 6). The
``place / on_tick / cancel_all`` surface and the Order/Fill dataclasses are
the contract both implementations share, so the trading engine cannot tell
them apart (design rule 6: paper is the same code path as live).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from trading_system.data.live_feed import Tick
from trading_system.logging_setup import get_logger
from trading_system.strategy.base import Action

log = get_logger(__name__)


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    id: str
    symbol: str
    instrument_key: str
    side: Action  # BUY or SELL
    qty: int
    reason: str = ""
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime | None = None


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    instrument_key: str
    side: Action
    qty: int
    price: float  # rupees, slippage already applied
    ts: datetime
    reason: str = ""


@dataclass
class PaperBroker:
    slippage_pct: float = 0.0003
    on_fill: Callable[[Fill], None] | None = None
    _pending: dict[str, Order] = field(default_factory=dict)  # one per symbol
    _seq: itertools.count = field(default_factory=lambda: itertools.count(1))

    def place(
        self, symbol: str, instrument_key: str, side: Action, qty: int, reason: str = ""
    ) -> Order:
        if side not in (Action.BUY, Action.SELL):
            raise ValueError(f"orders are BUY or SELL, got {side}")
        if qty < 1:
            raise ValueError(f"qty must be >= 1, got {qty}")
        if symbol in self._pending:
            raise RuntimeError(f"pending order already exists for {symbol}")
        order = Order(
            id=f"paper-{next(self._seq)}",
            symbol=symbol,
            instrument_key=instrument_key,
            side=side,
            qty=qty,
            reason=reason,
        )
        self._pending[symbol] = order
        log.info(
            "paper.order_placed",
            order_id=order.id, symbol=symbol, side=side.value, qty=qty, reason=reason,
        )
        return order

    def on_tick(self, tick: Tick) -> Fill | None:
        """Fill the pending order for this symbol, if any, at ltp +/- slippage."""
        order = self._pending.get(tick.symbol)
        if order is None:
            return None
        adj = 1 + self.slippage_pct if order.side == Action.BUY else 1 - self.slippage_pct
        fill = Fill(
            order_id=order.id,
            symbol=order.symbol,
            instrument_key=order.instrument_key,
            side=order.side,
            qty=order.qty,
            price=tick.ltp * adj,
            ts=tick.ltt,
            reason=order.reason,
        )
        order.status = OrderStatus.FILLED
        del self._pending[tick.symbol]
        log.info(
            "paper.filled",
            order_id=order.id, symbol=order.symbol, price=round(fill.price, 4),
        )
        if self.on_fill is not None:
            self.on_fill(fill)
        return fill

    def pending(self, symbol: str) -> Order | None:
        return self._pending.get(symbol)

    def cancel_all(self, symbol: str | None = None) -> int:
        symbols = [symbol] if symbol else list(self._pending)
        n = 0
        for sym in symbols:
            order = self._pending.pop(sym, None)
            if order is not None:
                order.status = OrderStatus.CANCELLED
                n += 1
                log.info("paper.cancelled", order_id=order.id, symbol=sym)
        return n
