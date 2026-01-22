import asyncio
import logging
import signal
from typing import Iterable

from pyrogram import filters, idle
from pyrogram.errors import FloodWait, RPCError

from logger_config import configure_logging

configure_logging()

from bot_instance import get_bot
from config import Config
from core import ban_queue, pre_ban_worker, start_preban_workers
from db import check_db_health, ensure_indexes, get_active_sessions, get_settings
from queue_handler import start_queue_monitor
from session_loader import save_session, test_all_sessions

from handlers import register_handlers
import payment_handler  # noqa: F401

LOGGER = logging.getLogger(__name__)
bot = get_bot()
HEALTH_LOG_INTERVAL = 60


async def _safe_reply(message, text: str) -> None:
    """Reply to a message, falling back to send_message on failure."""
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
    """Auto-validate session strings posted in the configured session group."""
    try:
        if not message.from_user or not message.text:
            return
        conf = await get_settings()
        if message.chat.id != conf.get("session_group"):
            return
        result = await save_session(message.text.strip())
        if result is True:
            active_sessions = await get_active_sessions()
            await _safe_reply(
                message,
                "✅ Session added successfully.\n"
                f"📊 Active Sessions: {len(active_sessions)}",
            )
        elif result is None:
            await _safe_reply(
                message,
                "⚠️ Session validated but failed to save. The database may be down.",
            )
        else:
            await _safe_reply(message, "❌ Session invalid or expired. Try again.")
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
    except RPCError:
        await _safe_reply(message, "❌ Session invalid or expired. Try again.")
    except Exception:
        LOGGER.exception("Auto session validation failed.")
        await _safe_reply(message, "❌ Session invalid or expired. Try again.")


async def _wait_for_db_ready() -> None:
    """Ensure DB is available or fallback to in-memory."""
    await check_db_health()


async def _health_logger() -> None:
    """Emit periodic health metrics to logs."""
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
    """Attach exception logging to background tasks."""
    for task in tasks:
        task.add_done_callback(_log_task_exception)


def _log_task_exception(task: asyncio.Task) -> None:
    """Log exceptions from background tasks."""
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
    """Main async entrypoint for the bot."""
    Config.validate()
    LOGGER.info("Config validation completed.")
    LOGGER.info("Initializing database connection.")
    await _wait_for_db_ready()
    await ensure_indexes()
    LOGGER.info("Database indexes ensured.")
    register_handlers(bot)
    LOGGER.info("Handlers registered.")
    await bot.start()
    LOGGER.info("Bot client started.")
    me = await bot.get_me()
    LOGGER.info(
        "Startup banner: name=%s owner_ids=%s api_id=%s",
        getattr(me, "first_name", "Unknown"),
        Config.OWNERS,
        Config.API_ID,
    )
    await test_all_sessions()
    active_sessions = await get_active_sessions()
    if not active_sessions:
        LOGGER.warning("⚠️ No sessions loaded yet. Waiting for sessions.")
    monitor_task = start_queue_monitor(ban_queue)
    LOGGER.info("Queue monitor started.")
    worker_tasks: list[asyncio.Task] = []
    supervisor: asyncio.Task | None = None
    background_tasks = [monitor_task]

    if active_sessions:
        worker_tasks = start_preban_workers(
            bot,
            num_workers=Config.PREBAN_WORKERS,
            session_concurrency=Config.SESSION_CONCURRENCY,
        )
        LOGGER.info("Pre-ban workers started: %s", len(worker_tasks))
        supervisor = asyncio.create_task(
            _supervise_workers(
                bot,
                worker_tasks,
                num_workers=Config.PREBAN_WORKERS,
                session_concurrency=Config.SESSION_CONCURRENCY,
            )
        )
        background_tasks.extend(worker_tasks)
        if supervisor:
            background_tasks.append(supervisor)

    health_task = asyncio.create_task(_health_logger())
    background_tasks.append(health_task)
    _attach_task_logger(background_tasks)
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

    async def _session_watchdog() -> None:
        """Wait for sessions to be added, then start workers."""
        nonlocal worker_tasks, supervisor
        if worker_tasks:
            return
        while not stop_event.is_set():
            try:
                sessions = await get_active_sessions()
                if sessions:
                    LOGGER.info("Sessions detected. Starting pre-ban workers.")
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
                    background_tasks.extend(worker_tasks)
                    if supervisor:
                        background_tasks.append(supervisor)
                    _attach_task_logger(
                        [*worker_tasks, supervisor] if supervisor else worker_tasks
                    )
                    LOGGER.info("Pre-ban workers started: %s", len(worker_tasks))
                    return
            except Exception:
                LOGGER.exception("Session watchdog failed.")
            await asyncio.sleep(15)

    watchdog_task = asyncio.create_task(_session_watchdog())
    background_tasks.append(watchdog_task)
    _attach_task_logger([watchdog_task])

    try:
        await idle()
    finally:
        stop_event.set()
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await bot.stop()


async def _supervise_workers(
    bot,
    worker_tasks: list[asyncio.Task],
    *,
    num_workers: int,
    session_concurrency: int,
) -> None:
    """Restart workers that crash or exit unexpectedly."""
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
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
