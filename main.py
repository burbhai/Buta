import asyncio
import logging

from pyrogram import filters
from pyrogram.errors import FloodWait, RPCError

from bot_instance import bot
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


async def _safe_reply(message, text: str) -> None:
    try:
        await message.reply(text)
    except Exception:
        LOGGER.exception("Failed to reply to message.")


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

async def main():
    Config.validate()
    if not await check_db_health():
        LOGGER.error("MongoDB is unavailable. Exiting.")
        raise SystemExit(1)
    await ensure_indexes()
    await bot.start()
    start_queue_monitor(ban_queue)
    worker_tasks = start_preban_workers(
        bot,
        num_workers=Config.PREBAN_WORKERS,
        session_concurrency=Config.SESSION_CONCURRENCY,
    )
    asyncio.create_task(
        _supervise_workers(
            bot,
            worker_tasks,
            num_workers=Config.PREBAN_WORKERS,
            session_concurrency=Config.SESSION_CONCURRENCY,
        )
    )
    LOGGER.info("Bot is running.")
    await asyncio.Event().wait()


async def _supervise_workers(
    bot,
    worker_tasks: list[asyncio.Task],
    *,
    num_workers: int,
    session_concurrency: int,
) -> None:
    while True:
        while len(worker_tasks) < num_workers:
            worker_tasks.append(
                asyncio.create_task(pre_ban_worker(bot, session_concurrency=session_concurrency))
            )
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
            worker_tasks[index] = asyncio.create_task(
                pre_ban_worker(bot, session_concurrency=session_concurrency)
            )
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
