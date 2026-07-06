from trading_system.strategy.base import Action, PositionState, Side, Signal, Strategy
from trading_system.strategy.ema_crossover import EmaCrossover
from trading_system.strategy.rsi_pullback import RsiPullback
from trading_system.strategy.supertrend_follow import SupertrendFollow

STRATEGIES: dict[str, type[Strategy]] = {
    "rsi_pullback": RsiPullback,
    "ema_crossover": EmaCrossover,
    "supertrend_follow": SupertrendFollow,
}

__all__ = [
    "Action",
    "PositionState",
    "Side",
    "Signal",
    "Strategy",
    "STRATEGIES",
    "RsiPullback",
    "EmaCrossover",
    "SupertrendFollow",
]
