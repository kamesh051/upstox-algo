"""Risk manager: every entry signal passes through here and can be vetoed.

Rules (all from RiskConfig):
- position sizing: floor(capital * max_capital_per_trade_pct / price)
- max concurrent positions
- mandatory stop-loss on every entry
- daily loss limit: once realized net P&L for the day breaches
  -daily_max_loss_pct * capital, no new entries until the next session.

The same instance is shared by backtest, paper, and live engines; engines call
``new_day`` at each session start and ``record_realized_pnl`` on every close.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date

from trading_system.config.settings import RiskConfig
from trading_system.logging_setup import get_logger
from trading_system.strategy.base import Action, Signal

log = get_logger(__name__)


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    qty: int = 0
    reason: str = ""


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self._day: date | None = None
        self._daily_realized_pnl_paise = 0
        self._halted = False
        self._entries_today: Counter[str] = Counter()

    # -- session lifecycle ---------------------------------------------------

    def new_day(self, day: date) -> None:
        if day != self._day:
            self._day = day
            self._daily_realized_pnl_paise = 0
            self._halted = False
            self._entries_today.clear()

    def record_entry(self, symbol: str) -> None:
        """Count an approved entry against the daily frequency caps."""
        self._entries_today[symbol] += 1

    def record_realized_pnl(self, net_pnl_paise: int) -> None:
        self._daily_realized_pnl_paise += net_pnl_paise
        limit = -int(self.cfg.capital_paise * self.cfg.daily_max_loss_pct)
        if not self._halted and self._daily_realized_pnl_paise <= limit:
            self._halted = True
            log.warning(
                "risk.daily_loss_halt",
                day=str(self._day),
                realized_paise=self._daily_realized_pnl_paise,
                limit_paise=limit,
            )

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def daily_realized_pnl_paise(self) -> int:
        return self._daily_realized_pnl_paise

    # -- entry approval --------------------------------------------------------

    def evaluate_entry(
        self, signal: Signal, symbol: str, price: float, open_positions: int
    ) -> RiskVerdict:
        """price: expected fill price in rupees. Exits are never vetoed."""
        if signal.action not in (Action.BUY, Action.SELL):
            return RiskVerdict(False, reason=f"not an entry signal: {signal.action}")
        if self._halted:
            return RiskVerdict(False, reason="daily loss limit hit — trading halted")
        if open_positions >= self.cfg.max_concurrent_positions:
            return RiskVerdict(
                False, reason=f"max concurrent positions ({self.cfg.max_concurrent_positions})"
            )
        gates = self.cfg.gates
        if gates.enabled:
            if self._entries_today[symbol] >= gates.max_trades_per_symbol_per_day:
                return RiskVerdict(
                    False,
                    reason=f"daily cap for {symbol} ({gates.max_trades_per_symbol_per_day}/day)",
                )
            if sum(self._entries_today.values()) >= gates.max_trades_per_day:
                return RiskVerdict(
                    False, reason=f"daily total cap ({gates.max_trades_per_day}/day)"
                )
        if self.cfg.mandatory_stop_loss and signal.stop_loss is None:
            return RiskVerdict(False, reason="entry without stop_loss rejected")
        if signal.stop_loss is not None:
            wrong_side = (
                signal.action == Action.BUY and signal.stop_loss >= price
            ) or (signal.action == Action.SELL and signal.stop_loss <= price)
            if wrong_side:
                return RiskVerdict(
                    False, reason=f"stop_loss {signal.stop_loss:.2f} on wrong side of price {price:.2f}"
                )

        qty = self.position_size(price)
        if qty < 1:
            return RiskVerdict(False, reason=f"price {price:.2f} too high for per-trade capital")
        return RiskVerdict(True, qty=qty)

    def position_size(self, price: float) -> int:
        per_trade_paise = int(self.cfg.capital_paise * self.cfg.max_capital_per_trade_pct)
        price_paise = round(price * 100)
        return per_trade_paise // price_paise if price_paise > 0 else 0
