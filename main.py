import asyncio
import logging
import signal
from typing import Iterable

from pyrogram import filters
from pyrogram.errors import FloodWait, RPCError

from bot_instance import get_bot
from config import Config
from core import ban_queue, pre_ban_worker, start_preban_workers
from db import check_db_health, ensure_indexes, get_active_sessions, get_settings
from queue_handler import start_queue_monitor
from session_loader import save_session

import handlers  # noqa: F401
import payment_handler  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger(__name__)
bot = get_bot()
HEALTH_LOG_INTERVAL = 60


async def _safe_reply(message, text: str) -> None:
    try:
        await message.reply(text)
    except Exception:
        LOGGER.exception("Failed to reply to message.")
        try:
            await message._client.send_message(message.chat.id, text)
        except Exception:
            LOGGER.exception("Failed to send fallback reply to chat.")


@bot.on_message(filters.text & filters.group)
async def auto_session_val(client, message):
    try:
        if not message.from_user or not message.text:
            return
        conf = await get_settings()
        if message.chat.id == conf.get("session_group") and message.from_user.id in Config.OWNERS:
            if await save_session(message.text.strip()):
                active_sessions = await get_active_sessions()
                await _safe_reply(
                    message,
                    "✅ Session Valid: added.\n"
                    f"📊 Active Sessions: {len(active_sessions)}",
                )
            else:
                await _safe_reply(message, "❌ Invalid Session.")
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
    except RPCError:
        await _safe_reply(message, "❌ Failed to validate session.")
    except Exception:
        LOGGER.exception("Auto session validation failed.")
        await _safe_reply(message, "❌ Failed to validate session.")


async def _wait_for_db_ready() -> None:
    attempt = 0
    while True:
        ok = await check_db_health()
        if ok:
            await ensure_indexes()
            return
        attempt += 1
        delay = min(60, 2**attempt)
        LOGGER.warning("MongoDB unavailable. Retrying in %s seconds.", delay)
        await asyncio.sleep(delay)


async def _health_logger() -> None:
    while True:
        try:
            db_ok = await check_db_health()
            sessions = await get_active_sessions()
            LOGGER.info(
                "Health check: db=%s sessions=%s",
                "ok" if db_ok else "fail",
                len(sessions),
            )
        except Exception:
            LOGGER.exception("Health check logging failed.")
        await asyncio.sleep(HEALTH_LOG_INTERVAL)


def _attach_task_logger(tasks: Iterable[asyncio.Task]) -> None:
    for task in tasks:
        task.add_done_callback(_log_task_exception)


def _log_task_exception(task: asyncio.Task) -> None:
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    except Exception:
        LOGGER.exception("Failed to fetch task exception.")
        return
    if exc:
        LOGGER.error("Background task failed.", exc_info=exc)

async def main():
    Config.validate()
    await bot.start()
    await _wait_for_db_ready()
    monitor_task = start_queue_monitor(ban_queue)
    worker_tasks = start_preban_workers(
        bot,
        num_workers=Config.PREBAN_WORKERS,
        session_concurrency=Config.SESSION_CONCURRENCY,
    )
    supervisor = asyncio.create_task(
        _supervise_workers(
            bot,
            worker_tasks,
            num_workers=Config.PREBAN_WORKERS,
            session_concurrency=Config.SESSION_CONCURRENCY,
        )
    )
    health_task = asyncio.create_task(_health_logger())
    _attach_task_logger([monitor_task, supervisor, health_task, *worker_tasks])
    LOGGER.info("Bot is running.")
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        LOGGER.info("Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        for task in [monitor_task, supervisor, health_task, *worker_tasks]:
            task.cancel()
        await bot.stop()


async def _supervise_workers(
    bot,
    worker_tasks: list[asyncio.Task],
    *,
    num_workers: int,
    session_concurrency: int,
) -> None:
    while True:
        while len(worker_tasks) < num_workers:
            task = asyncio.create_task(
                pre_ban_worker(bot, session_concurrency=session_concurrency)
            )
            _attach_task_logger([task])
            worker_tasks.append(task)
        for index, task in enumerate(list(worker_tasks)):
            if not task.done():
                continue
            if task.cancelled():
                LOGGER.error("Pre-ban worker %s was cancelled. Restarting.", index)
            else:
                exc = task.exception()
                if exc:
                    LOGGER.error("Pre-ban worker %s crashed. Restarting.", index, exc_info=exc)
                else:
                    LOGGER.error("Pre-ban worker %s exited unexpectedly. Restarting.", index)
            replacement = asyncio.create_task(
                pre_ban_worker(bot, session_concurrency=session_concurrency)
            )
            _attach_task_logger([replacement])
            worker_tasks[index] = replacement
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
