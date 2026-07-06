"""CLI entry point.

Phase 1 commands:
    python main.py auth                      # daily OAuth login
    python main.py instruments [--force]     # refresh instruments master
    python main.py download --interval 15minute --days 365
    python main.py download --from 2025-07-01 --to 2026-07-01 --symbols RELIANCE,TCS
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from trading_system.auth import AuthError, TokenStore, run_login_flow
from trading_system.config import AppConfig, Secrets, load_config
from trading_system.data.historical import CandleStore, HistoricalDownloader
from trading_system.data.instruments import InstrumentStore
from trading_system.logging_setup import get_logger, setup_logging
from trading_system.ratelimit import TokenBucket

TOKEN_PATH = Path(".secrets") / "token.json"

log = get_logger("main")


def _token_store() -> TokenStore:
    return TokenStore(TOKEN_PATH)


def _require_token(secrets: Secrets) -> str:
    """Env-var token wins; otherwise the stored one if still valid."""
    if secrets.upstox_access_token:
        return secrets.upstox_access_token
    token = _token_store().get_valid_token()
    if token is None:
        raise AuthError(
            "No valid access token (they expire daily at ~03:30 IST). "
            "Run `python main.py auth` first, or set UPSTOX_ACCESS_TOKEN."
        )
    return token


def cmd_auth(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
    run_login_flow(
        api_key=secrets.upstox_api_key,
        api_secret=secrets.upstox_api_secret,
        redirect_uri=secrets.upstox_redirect_uri,
        token_store=_token_store(),
    )
    print("Access token saved. Valid until ~03:30 IST tomorrow.")
    return 0


def cmd_instruments(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
    store = InstrumentStore(cfg.data.cache_dir, cfg.data.instruments_max_age_days)
    store.refresh(force=args.force)
    resolved = store.resolve_many(cfg.symbols)
    print(f"{'SYMBOL':<12} INSTRUMENT_KEY")
    for symbol, key in resolved.items():
        print(f"{symbol:<12} {key}")
    return 0


def cmd_download(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
    if args.from_date:
        from_date, to_date = args.from_date, args.to_date or date.today()
    else:
        to_date = date.today()
        from_date = to_date - timedelta(days=args.days)

    symbols = args.symbols.split(",") if args.symbols else cfg.symbols

    instruments = InstrumentStore(cfg.data.cache_dir, cfg.data.instruments_max_age_days)
    keys = instruments.resolve_many(symbols)

    token = _require_token(secrets)
    candle_store = CandleStore(cfg.data.db_path)
    downloader = HistoricalDownloader(
        store=candle_store,
        token_provider=lambda: token,
        api_base=cfg.data.api_base,
        rate_limiter=TokenBucket(cfg.data.rate_limit_per_sec, cfg.data.rate_limit_burst),
    )

    print(f"Downloading {args.interval} candles {from_date} -> {to_date}\n")
    for symbol, key in keys.items():
        n = downloader.download(key, args.interval, from_date, to_date)
        total = candle_store.count(key, args.interval)
        print(f"  {symbol:<12} fetched {n:>7,} candles   (cache now holds {total:,})")
    candle_store.close()
    print(f"\nCache: {cfg.data.db_path}")
    return 0


def cmd_backtest(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
    from trading_system.backtest.engine import BacktestEngine
    from trading_system.backtest.metrics import format_summary
    from trading_system.backtest.report import write_report
    from trading_system.data.instruments import InstrumentStore
    from trading_system.risk import RiskManager
    from trading_system.strategy import STRATEGIES

    if args.strategy not in STRATEGIES:
        print(f"Unknown strategy {args.strategy!r}. Available: {', '.join(STRATEGIES)}")
        return 2

    from_date = args.from_date
    to_date = args.to_date or date.today()
    symbols = args.symbols.split(",") if args.symbols else cfg.symbols
    if args.no_gates:
        cfg.risk.gates.enabled = False

    instruments = InstrumentStore(cfg.data.cache_dir, cfg.data.instruments_max_age_days)
    keys = instruments.resolve_many(symbols)

    store = CandleStore(cfg.data.db_path)
    data: dict[str, object] = {}
    for symbol, key in keys.items():
        # load everything so pre-from_date candles can warm up the indicators
        df = store.load(key, args.interval, to_date=to_date)
        if df.empty:
            print(f"No cached candles for {symbol} ({args.interval}). Run `download` first.")
            return 2
        data[symbol] = df
    store.close()

    engine = BacktestEngine(
        strategy_cls=STRATEGIES[args.strategy],
        risk_manager=RiskManager(cfg.risk),
        initial_capital_paise=cfg.risk.capital_paise,
        slippage_pct=cfg.backtest.slippage_pct,
        square_off_time=cfg.backtest.square_off_time,
        no_new_entries_after=cfg.backtest.no_new_entries_after,
        gates=cfg.risk.gates,
    )
    result = engine.run(data, from_date, to_date)

    suffix = "" if cfg.risk.gates.enabled else "_nogates"
    out_dir = cfg.backtest.reports_dir / f"{args.strategy}_{from_date}_{to_date}{suffix}"
    metrics = write_report(result, out_dir)
    print()
    print(format_summary(metrics))
    print(f"\nReport files in {out_dir}")
    return 0


def _build_seeds(cfg: AppConfig, keys: dict[str, str], token: str, interval: str) -> dict:
    """Fresh IndicatorStream per symbol: cache backfill + today's intraday candles."""
    from trading_system.data.candles import IndicatorStream
    from trading_system.data.historical import HistoricalDownloader, build_live_seed

    store = CandleStore(cfg.data.db_path)
    downloader = HistoricalDownloader(
        store=store,
        token_provider=lambda: token,
        api_base=cfg.data.api_base,
        rate_limiter=TokenBucket(cfg.data.rate_limit_per_sec, cfg.data.rate_limit_burst),
    )
    streams = {}
    for symbol, key in keys.items():
        seed = build_live_seed(store, downloader, key, interval)
        streams[symbol] = IndicatorStream(seed if not seed.empty else None)
        last = seed.index[-1] if not seed.empty else "EMPTY"
        print(f"seeded {symbol} {interval}: {len(streams[symbol])} candles (through {last})")
    store.close()
    return streams


def cmd_feed(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
    import asyncio

    from trading_system.data.instruments import InstrumentStore
    from trading_system.data.live_feed import LiveFeed

    symbols = args.symbols.split(",") if args.symbols else cfg.symbols
    instruments = InstrumentStore(cfg.data.cache_dir, cfg.data.instruments_max_age_days)
    keys = instruments.resolve_many(symbols)
    token = _require_token(secrets)

    feed = LiveFeed(
        access_token=token,
        key_to_symbol={key: sym for sym, key in keys.items()},
        cfg=cfg.feed,
    )

    async def stream() -> None:
        runner = asyncio.create_task(feed.run())
        print(f"Live feed ({cfg.feed.mode}): {', '.join(symbols)}   Ctrl+C to stop\n")
        try:
            async for tick in feed.ticks():
                print(
                    f"{tick.ltt:%H:%M:%S}  {tick.symbol:<12} "
                    f"ltp {tick.ltp:>10.2f}  qty {tick.ltq:>6}"
                )
        finally:
            feed.stop()
            await runner

    try:
        asyncio.run(stream())
    except KeyboardInterrupt:
        feed.stop()
        print("\nFeed stopped.")
    return 0


def _fmt_indicator(value) -> str:
    import math

    return f"{value:.2f}" if value is not None and not math.isnan(value) else "-"


def cmd_stream(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
    import asyncio

    from trading_system.data.candles import CandleBuilder, IndicatorStream
    from trading_system.data.instruments import InstrumentStore
    from trading_system.data.live_feed import LiveFeed

    intervals = args.intervals.split(",")
    symbols = args.symbols.split(",") if args.symbols else cfg.symbols
    instruments = InstrumentStore(cfg.data.cache_dir, cfg.data.instruments_max_age_days)
    keys = instruments.resolve_many(symbols)
    token = _require_token(secrets)

    # fresh seed per (symbol, interval): cache backfill + today's intraday candles
    streams: dict[tuple[str, str], IndicatorStream] = {}
    for interval in intervals:
        for symbol, stream in _build_seeds(cfg, keys, token, interval).items():
            streams[(symbol, interval)] = stream

    feed = LiveFeed(
        access_token=token,
        key_to_symbol={key: sym for sym, key in keys.items()},
        cfg=cfg.feed,
    )
    builder = CandleBuilder(intervals)

    async def stream() -> None:
        runner = asyncio.create_task(feed.run())
        print(f"Live candles {intervals}: {', '.join(symbols)}   Ctrl+C to stop\n")
        try:
            async for tick in feed.ticks():
                for candle in builder.on_tick(tick):
                    row = streams[(candle.symbol, candle.interval)].append(candle)
                    print(
                        f"{candle.ts:%H:%M} {candle.interval:<8} {candle.symbol:<10} "
                        f"O {candle.open:>9.2f} H {candle.high:>9.2f} "
                        f"L {candle.low:>9.2f} C {candle.close:>9.2f} V {candle.volume:>8} | "
                        f"RSI {_fmt_indicator(row['rsi14'])} "
                        f"EMA20 {_fmt_indicator(row['ema20'])} "
                        f"VWAP {_fmt_indicator(row['vwap'])} "
                        f"ST {'+' if row['st_dir'] == 1 else '-'}{_fmt_indicator(row['supertrend'])}"
                    )
        finally:
            feed.stop()
            await runner

    try:
        asyncio.run(stream())
    except KeyboardInterrupt:
        feed.stop()
        print("\nStream stopped.")
    return 0


def cmd_paper(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
    import asyncio

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    from trading_system.auth.token_store import IST
    from trading_system.data.instruments import InstrumentStore
    from trading_system.data.live_feed import LiveFeed
    from trading_system.engine import TradingEngine
    from trading_system.execution import PaperBroker
    from trading_system.monitor import TelegramNotifier, heartbeat
    from trading_system.risk import RiskManager
    from trading_system.strategy import STRATEGIES

    strategy_name = args.strategy or cfg.paper.strategy
    if strategy_name not in STRATEGIES:
        print(f"Unknown strategy {strategy_name!r}. Available: {', '.join(STRATEGIES)}")
        return 2
    symbols = args.symbols.split(",") if args.symbols else cfg.symbols
    instruments = InstrumentStore(cfg.data.cache_dir, cfg.data.instruments_max_age_days)
    keys = instruments.resolve_many(symbols)
    token = _require_token(secrets)

    dashboard_on = args.dashboard or cfg.ui.enabled
    if dashboard_on:
        from trading_system.events import AsyncQueueBus

        bus = AsyncQueueBus(maxsize=cfg.ui.event_queue_size)
        setup_logging(cfg.logging.level, cfg.logging.json_output, bus=bus)
    else:
        from trading_system.events import NullBus

        bus = NullBus()

    notifier = TelegramNotifier(secrets.telegram_bot_token, secrets.telegram_chat_id)

    def alert(msg: str) -> None:
        print(f"[ALERT] {msg}")
        notifier.send_soon(msg)

    streams = _build_seeds(cfg, keys, token, cfg.paper.interval)

    broker = PaperBroker(slippage_pct=cfg.paper.slippage_pct)
    engine = TradingEngine(
        strategy_cls=STRATEGIES[strategy_name],
        risk=RiskManager(cfg.risk),
        broker=broker,
        streams=streams,
        symbol_to_key=keys,
        initial_capital_paise=cfg.risk.capital_paise,
        interval=cfg.paper.interval,
        square_off_time=cfg.backtest.square_off_time,
        no_new_entries_after=cfg.backtest.no_new_entries_after,
        gates=cfg.risk.gates,
        alert=alert,
        bus=bus,
    )
    feed = LiveFeed(
        access_token=token,
        key_to_symbol={key: sym for sym, key in keys.items()},
        cfg=cfg.feed,
        alert=alert,
        bus=bus,
    )

    def write_eod_report() -> dict:
        report = engine.day_report()
        out_dir = cfg.backtest.reports_dir / "paper" / (report["date"] or str(date.today()))
        out_dir.mkdir(parents=True, exist_ok=True)
        if engine.trades:
            import pandas as pd

            pd.DataFrame([t.__dict__ for t in engine.trades]).to_csv(
                out_dir / "trades.csv", index=False
            )
        summary = "\n".join(f"{k}: {v}" for k, v in report.items())
        (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
        print(f"\n=== EOD paper report ===\n{summary}\nSaved to {out_dir}")
        return report

    async def session() -> None:
        stop = asyncio.Event()

        async def eod_and_stop() -> None:
            engine.square_off_all("end of session")
            await asyncio.sleep(5)  # let exit orders fill on remaining ticks
            report = write_eod_report()
            await notifier.send(
                f"EOD paper report {report['date']}: {report['trades']} trades, "
                f"net Rs {report['net_pnl_rupees']:,.2f}, costs Rs {report['costs_rupees']:,.2f}"
            )
            stop.set()

        scheduler = AsyncIOScheduler(timezone=IST)
        scheduler.add_job(  # failsafe: tick-side square-off already fires at 15:15
            engine.square_off_all, CronTrigger(hour=15, minute=16, timezone=IST),
            kwargs={"reason": "scheduler square-off failsafe"},
        )
        scheduler.add_job(eod_and_stop, CronTrigger(hour=15, minute=31, timezone=IST))
        scheduler.start()

        server_task = None
        if dashboard_on:
            from dashboard.server import create_app, serve
            from dashboard.state import build_state_provider, build_trades_provider

            app = create_app(
                bus,
                build_state_provider(
                    engine, feed, _token_store(), notifier.enabled, cfg, bus, mode="paper"
                ),
                build_trades_provider(engine),
            )
            server_task = asyncio.create_task(serve(app, cfg.ui.host, cfg.ui.port))
            print(f"Dashboard: http://{cfg.ui.host}:{cfg.ui.port}")

        runner = asyncio.create_task(feed.run())
        consumer = asyncio.create_task(engine.run(feed))
        hb = asyncio.create_task(heartbeat(engine, notifier, cfg.paper.heartbeat_minutes))
        await notifier.send(
            f"Paper session up: {strategy_name} on {', '.join(symbols)} "
            f"({cfg.paper.interval}, capital Rs {cfg.risk.capital_paise / 100:,.0f})"
        )
        print("Paper session running. Square-off 15:15, EOD report 15:31. Ctrl+C to stop early.\n")
        try:
            await stop.wait()
        finally:
            scheduler.shutdown(wait=False)
            for task in (hb, consumer):
                task.cancel()
            if server_task is not None:
                server_task.cancel()
            feed.stop()
            await runner

    try:
        asyncio.run(session())
    except KeyboardInterrupt:
        feed.stop()
        print("\nInterrupted — writing EOD report for the partial session.")
        write_eod_report()
    return 0


def cmd_dashboard_demo(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
    """Replay cached sessions through the real engine + bus with the dashboard up."""
    import asyncio
    from datetime import timedelta
    from types import SimpleNamespace

    from dashboard.server import create_app, serve
    from dashboard.state import build_state_provider, build_trades_provider
    from trading_system.data.candles import IndicatorStream
    from trading_system.data.instruments import InstrumentStore
    from trading_system.data.live_feed import Tick
    from trading_system.engine import TradingEngine
    from trading_system.events import AsyncQueueBus
    from trading_system.execution import PaperBroker
    from trading_system.risk import RiskManager
    from trading_system.strategy import STRATEGIES

    bus = AsyncQueueBus(maxsize=cfg.ui.event_queue_size)
    setup_logging(cfg.logging.level, cfg.logging.json_output, bus=bus)

    instruments = InstrumentStore(cfg.data.cache_dir, cfg.data.instruments_max_age_days)
    keys = instruments.resolve_many(cfg.symbols)
    store = CandleStore(cfg.data.db_path)
    frames = {sym: store.load(key, cfg.paper.interval) for sym, key in keys.items()}
    store.close()

    all_days = sorted({d for df in frames.values() for d in df.index.date})
    replay_days = all_days[-args.sessions :]
    streams, ticks = {}, []
    for sym, df in frames.items():
        streams[sym] = IndicatorStream(df[df.index.date < replay_days[0]])
        for ts, row in df[df.index.date >= replay_days[0]].iterrows():
            vol = int(row["volume"]) // 4
            for offset, px in (
                (5, row["open"]), (300, row["high"]), (600, row["low"]), (885, row["close"]),
            ):
                t = ts + timedelta(seconds=offset)
                ticks.append(Tick(keys[sym], sym, float(px), vol, t, t))
    ticks.sort(key=lambda t: t.ltt)

    engine = TradingEngine(
        strategy_cls=STRATEGIES[cfg.paper.strategy],
        risk=RiskManager(cfg.risk),
        broker=PaperBroker(slippage_pct=cfg.paper.slippage_pct),
        streams=streams,
        symbol_to_key=keys,
        initial_capital_paise=cfg.risk.capital_paise,
        interval=cfg.paper.interval,
        square_off_time=cfg.backtest.square_off_time,
        no_new_entries_after=cfg.backtest.no_new_entries_after,
        gates=cfg.risk.gates,
        alert=lambda msg: print(f"[ALERT] {msg}"),
        bus=bus,
    )
    feed_shim = SimpleNamespace(status="open")  # demo has no real socket
    app = create_app(
        bus,
        build_state_provider(engine, feed_shim, _token_store(), False, cfg, bus, mode="paper"),
        build_trades_provider(engine),
    )

    async def run() -> None:
        server = asyncio.create_task(serve(app, cfg.ui.host, cfg.ui.port))
        print(
            f"Demo dashboard: http://{cfg.ui.host}:{cfg.ui.port}\n"
            f"Replaying {len(ticks)} ticks over {[str(d) for d in replay_days]} "
            f"at {args.speed}/s. Ctrl+C to stop.\n"
        )
        await asyncio.sleep(1)  # let the server bind before events start
        for t in ticks:
            engine.on_tick(t)
            await asyncio.sleep(1 / args.speed)
        print("\nReplay finished — dashboard stays up for inspection. Ctrl+C to exit.")
        try:
            await asyncio.Event().wait()
        finally:
            server.cancel()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nDemo stopped.")
    return 0


def cmd_not_implemented(phase: str):
    def handler(cfg: AppConfig, secrets: Secrets, args: argparse.Namespace) -> int:
        print(f"'{args.command}' is not implemented yet ({phase}).")
        return 2

    return handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="upstox-algo")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="Run the daily OAuth login flow")

    p_instr = sub.add_parser("instruments", help="Refresh/inspect instruments master")
    p_instr.add_argument("--force", action="store_true", help="Re-download even if fresh")

    p_dl = sub.add_parser("download", help="Download historical candles into the cache")
    p_dl.add_argument("--interval", default="15minute")
    p_dl.add_argument("--days", type=int, default=365, help="Lookback from today")
    p_dl.add_argument("--from", dest="from_date", type=date.fromisoformat, default=None)
    p_dl.add_argument("--to", dest="to_date", type=date.fromisoformat, default=None)
    p_dl.add_argument("--symbols", default=None, help="Comma-separated; default from config")

    p_bt = sub.add_parser("backtest", help="Run a backtest on cached candles")
    p_bt.add_argument("--strategy", required=True)
    p_bt.add_argument("--interval", default="15minute")
    p_bt.add_argument("--from", dest="from_date", type=date.fromisoformat, required=True)
    p_bt.add_argument("--to", dest="to_date", type=date.fromisoformat, default=None)
    p_bt.add_argument("--symbols", default=None, help="Comma-separated; default from config")
    p_bt.add_argument(
        "--no-gates", dest="no_gates", action="store_true",
        help="Disable trade-frequency gates (baseline/Phase 2 behavior)",
    )

    p_feed = sub.add_parser("feed", help="Print live ticks from the market data WebSocket")
    p_feed.add_argument("--symbols", default=None, help="Comma-separated; default from config")

    p_stream = sub.add_parser("stream", help="Print live candles + indicators (Phase 3 deliverable)")
    p_stream.add_argument("--symbols", default=None, help="Comma-separated; default from config")
    p_stream.add_argument(
        "--intervals", default="1minute", help="Comma-separated: 1minute,5minute,15minute"
    )

    p_paper = sub.add_parser("paper", help="Run an unattended paper trading session")
    p_paper.add_argument("--strategy", default=None, help="Default from config (paper.strategy)")
    p_paper.add_argument("--symbols", default=None, help="Comma-separated; default from config")
    p_paper.add_argument(
        "--dashboard", action="store_true",
        help="Serve the monitoring dashboard (also via ui.enabled in config)",
    )

    p_demo = sub.add_parser(
        "dashboard-demo",
        help="Dashboard on replayed cached sessions (develop/verify UI offline)",
    )
    p_demo.add_argument("--sessions", type=int, default=2, help="Cached days to replay")
    p_demo.add_argument("--speed", type=float, default=25.0, help="Ticks per second")
    sub.add_parser("live", help="(Phase 6)")
    return parser


HANDLERS = {
    "auth": cmd_auth,
    "instruments": cmd_instruments,
    "download": cmd_download,
    "backtest": cmd_backtest,
    "feed": cmd_feed,
    "stream": cmd_stream,
    "dashboard-demo": cmd_dashboard_demo,
    "paper": cmd_paper,
    "live": cmd_not_implemented("Phase 6"),
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.logging.level, cfg.logging.json_output)
    secrets = Secrets()
    try:
        return HANDLERS[args.command](cfg, secrets, args)
    except AuthError as e:
        print(f"\nAuth error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
