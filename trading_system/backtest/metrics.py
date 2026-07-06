"""Performance metrics computed from trades + the equity curve (paise domain)."""

from __future__ import annotations

import math

from trading_system.backtest.engine import BacktestResult

TRADING_DAYS_PER_YEAR = 252


def compute_metrics(result: BacktestResult) -> dict:
    trades = result.trades
    equity = result.equity
    capital = result.initial_capital_paise

    wins = [t for t in trades if t.net_pnl_paise > 0]
    losses = [t for t in trades if t.net_pnl_paise <= 0]
    gross_profit = sum(t.net_pnl_paise for t in wins)
    gross_loss = -sum(t.net_pnl_paise for t in losses)
    net_pnl = sum(t.net_pnl_paise for t in trades)
    total_costs = sum(t.costs_paise for t in trades)

    # daily returns for Sharpe: last equity mark per session
    if len(equity) > 1:
        daily = equity.groupby(equity.index.date).last()
        rets = daily.pct_change().dropna()
        sharpe = (
            float(rets.mean() / rets.std() * math.sqrt(TRADING_DAYS_PER_YEAR))
            if len(rets) > 1 and rets.std() > 0
            else 0.0
        )
        peak = equity.cummax()
        max_dd_pct = float(((equity - peak) / peak).min()) * 100
    else:
        sharpe = 0.0
        max_dd_pct = 0.0

    return {
        "strategy": result.strategy,
        "from_date": result.from_date.isoformat(),
        "to_date": result.to_date.isoformat(),
        "initial_capital_rupees": capital / 100,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(trades), 2) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else None,
        "net_pnl_rupees": net_pnl / 100,
        "return_pct": round(100 * net_pnl / capital, 3),
        "total_costs_rupees": total_costs / 100,
        # expectancy per trade after costs — THE optimization target (Phase 2.5);
        # identical to avg trade since costs are already inside net P&L
        "expectancy_rupees": round(net_pnl / len(trades) / 100, 2) if trades else 0.0,
        "avg_trade_rupees": round(net_pnl / len(trades) / 100, 2) if trades else 0.0,
        "avg_win_rupees": round(gross_profit / len(wins) / 100, 2) if wins else 0.0,
        "avg_loss_rupees": round(-gross_loss / len(losses) / 100, 2) if losses else 0.0,
        "best_trade_rupees": max((t.net_pnl_paise for t in trades), default=0) / 100,
        "worst_trade_rupees": min((t.net_pnl_paise for t in trades), default=0) / 100,
        "max_drawdown_pct": round(max_dd_pct, 3),
        "sharpe_ratio": round(sharpe, 3),
        "rejected_entries": result.rejected_entries,
    }


def format_summary(metrics: dict) -> str:
    """Plain-text report block (ASCII only — Windows console is cp1252)."""
    lines = [
        f"Backtest: {metrics['strategy']}  {metrics['from_date']} -> {metrics['to_date']}",
        f"Capital: Rs {metrics['initial_capital_rupees']:,.0f}",
        "-" * 56,
        f"Trades: {metrics['total_trades']}  (wins {metrics['wins']} / losses {metrics['losses']}, "
        f"win rate {metrics['win_rate_pct']}%)",
        f"Net P&L: Rs {metrics['net_pnl_rupees']:,.2f}  ({metrics['return_pct']:+.2f}%)",
        f"Costs paid: Rs {metrics['total_costs_rupees']:,.2f}",
        f"Profit factor: {metrics['profit_factor']}",
        f"Expectancy: Rs {metrics['expectancy_rupees']:,.2f}/trade  "
        f"(avg win Rs {metrics['avg_win_rupees']:,.2f} / avg loss Rs {metrics['avg_loss_rupees']:,.2f})",
        f"Best Rs {metrics['best_trade_rupees']:,.2f} / worst Rs {metrics['worst_trade_rupees']:,.2f}",
        f"Max drawdown: {metrics['max_drawdown_pct']}%",
        f"Sharpe (daily, annualized): {metrics['sharpe_ratio']}",
        f"Entries rejected by risk manager: {metrics['rejected_entries']}",
    ]
    return "\n".join(lines)
