"""Telegram notifications + heartbeat.

Alerting must NEVER block or crash trading: missing credentials degrade to
console logging, and send failures are swallowed (logged) — the trading loop
does not await alert delivery.
"""

from __future__ import annotations

import asyncio

from trading_system.logging_setup import get_logger

log = get_logger(__name__)


class TelegramNotifier:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.chat_id = chat_id
        self._bot = None
        if token and chat_id:
            from telegram import Bot  # imported lazily: optional at runtime

            self._bot = Bot(token=token)
        else:
            log.info("alerts.telegram_disabled", reason="missing token/chat_id in .env")

    @property
    def enabled(self) -> bool:
        return self._bot is not None

    async def send(self, text: str) -> None:
        """Deliver a message; failures are logged, never raised."""
        log.info("alert", message=text)
        if self._bot is None:
            return
        try:
            await self._bot.send_message(chat_id=self.chat_id, text=text)
        except Exception as e:
            log.warning("alerts.telegram_send_failed", error=str(e))

    def send_soon(self, text: str) -> None:
        """Fire-and-forget from sync code inside a running event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.send(text))
            return
        loop.create_task(self.send(text))


async def heartbeat(engine, notifier: TelegramNotifier, interval_minutes: float) -> None:
    """Periodic liveness message with position count and day P&L."""
    while True:
        await asyncio.sleep(interval_minutes * 60)
        last = engine._last_tick
        tick_info = f"last tick {last.ltt:%H:%M:%S} {last.symbol}" if last else "no ticks yet"
        await notifier.send(
            f"heartbeat | positions {len(engine.open_positions)} | "
            f"day P&L Rs {engine.realized_pnl_paise / 100:,.2f} | "
            f"trades {len(engine.trades)} | {tick_info}"
        )
