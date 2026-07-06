import asyncio

import pytest

from trading_system.monitor import TelegramNotifier, heartbeat


class RecordingBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class FailingBot:
    async def send_message(self, chat_id, text):
        raise RuntimeError("telegram down")


@pytest.mark.asyncio
async def test_disabled_without_credentials():
    notifier = TelegramNotifier("", "")
    assert not notifier.enabled
    await notifier.send("hello")  # no crash, logs only


@pytest.mark.asyncio
async def test_send_delivers():
    notifier = TelegramNotifier("", "")
    notifier._bot, notifier.chat_id = RecordingBot(), "42"
    await notifier.send("trade alert")
    assert notifier._bot.messages == [("42", "trade alert")]


@pytest.mark.asyncio
async def test_send_failure_swallowed():
    notifier = TelegramNotifier("", "")
    notifier._bot, notifier.chat_id = FailingBot(), "42"
    await notifier.send("boom")  # must not raise — alerts never crash trading


@pytest.mark.asyncio
async def test_heartbeat_message():
    notifier = TelegramNotifier("", "")
    bot = RecordingBot()
    notifier._bot, notifier.chat_id = bot, "42"

    class StubEngine:
        _last_tick = None
        open_positions = {}
        realized_pnl_paise = -12345
        trades = []

    task = asyncio.create_task(heartbeat(StubEngine(), notifier, interval_minutes=0.0005))
    await asyncio.sleep(0.2)
    task.cancel()
    assert bot.messages, "heartbeat never fired"
    text = bot.messages[0][1]
    assert "heartbeat" in text and "-123.45" in text and "no ticks yet" in text
