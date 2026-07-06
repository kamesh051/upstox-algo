"""Backtest report outputs: trades.csv, equity_curve.png, metrics.json, summary.txt."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless — never try to open a window
import matplotlib.pyplot as plt

from trading_system.backtest.engine import BacktestResult
from trading_system.backtest.metrics import compute_metrics, format_summary
from trading_system.logging_setup import get_logger

log = get_logger(__name__)


def write_report(result: BacktestResult, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = compute_metrics(result)

    trades_df = result.trades_frame()
    trades_df.to_csv(out_dir / "trades.csv", index=False)

    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.txt").write_text(format_summary(metrics), encoding="utf-8")

    fig, ax = plt.subplots(figsize=(11, 5))
    equity_rupees = result.equity / 100
    ax.plot(result.equity.index, equity_rupees, linewidth=1.2)
    ax.axhline(
        result.initial_capital_paise / 100, linestyle="--", linewidth=0.8, alpha=0.6
    )
    ax.set_title(
        f"{result.strategy}  {result.from_date} to {result.to_date}  "
        f"(net {metrics['return_pct']:+.2f}%)"
    )
    ax.set_ylabel("Equity (Rs)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_dir / "equity_curve.png", dpi=120)
    plt.close(fig)

    log.info("report.written", dir=str(out_dir))
    return metrics
