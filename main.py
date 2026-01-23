import asyncio
import logging
import signal
from typing import Iterable

from pyrogram import idle

from logger_config import configure_logging

configure_logging()

from bot_instance import get_bot
from config import Config
from core import ban_queue, start_preban_workers
from core_fixes import patch_pyrogram_peer_type
from db import check_db_health, ensure_indexes, get_active_sessions
from handlers import register_fallbacks, register_ui_and_commands
from payment_handler import register_payment
from queue_handler import start_queue_monitor
from session_loader import register_session_ingest, test_all_sessions

LOGGER = logging.getLogger(__name__)
HEALTH_LOG_INTERVAL = 60


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


async def main() -> None:
    """Main async entrypoint for the bot."""
    Config.validate()
    LOGGER.info("Config validation completed.")
    patch_pyrogram_peer_type()
    LOGGER.info("Patched Pyrogram peer type detection.")
    LOGGER.info("Initializing database connection.")
    await _wait_for_db_ready()
    await ensure_indexes()
    LOGGER.info("Database indexes ensured.")

    bot = get_bot()
    await bot.start()
    LOGGER.info("Bot client started.")

    register_ui_and_commands(bot)
    register_payment(bot)
    register_session_ingest(bot)
    register_fallbacks(bot)
    LOGGER.info("Handlers registered.")

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
                    return
            except Exception:
                LOGGER.exception("Session watchdog failed.")
            await asyncio.sleep(10)

    watchdog_task = asyncio.create_task(_session_watchdog())
    background_tasks.append(watchdog_task)

    await idle()
    LOGGER.info("Shutdown initiated.")
    stop_event.set()

    for task in background_tasks:
        task.cancel()
    await bot.stop()
    LOGGER.info("Bot client stopped.")


async def _supervise_workers(
    bot,
    tasks: list[asyncio.Task],
    *,
    num_workers: int,
    session_concurrency: int,
) -> None:
    while True:
        await asyncio.sleep(5)
        if all(task.done() for task in tasks):
            LOGGER.warning("All workers stopped unexpectedly. Restarting.")
            tasks[:] = start_preban_workers(
                bot,
                num_workers=num_workers,
                session_concurrency=session_concurrency,
            )


if __name__ == "__main__":
    asyncio.run(main())
