import asyncio
import logging

from pyrogram import filters
from pyrogram.errors import FloodWait, RPCError

from bot_instance import bot
from config import Config
from core import ban_queue, start_preban_workers
from db import ensure_indexes, get_active_sessions, get_settings
from queue_handler import start_queue_monitor
from session_loader import save_session

import handlers  # noqa: F401
import payment_handler  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger(__name__)

@bot.on_message(filters.text & filters.group)
async def auto_session_val(client, message):
    try:
        if not message.from_user or not message.text:
            return
        conf = await get_settings()
        if message.chat.id == conf.get("session_group") and message.from_user.id in Config.OWNERS:
            if await save_session(message.text.strip()):
                active_sessions = await get_active_sessions()
                await message.reply(
                    "✅ Session Valid: added.\n"
                    f"📊 Active Sessions: {len(active_sessions)}"
                )
            else:
                await message.reply("❌ Invalid Session.")
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
    except RPCError:
        await message.reply("❌ Failed to validate session.")
    except Exception:
        LOGGER.exception("Auto session validation failed.")
        await message.reply("❌ Failed to validate session.")

async def main():
    await ensure_indexes()
    await bot.start()
    start_queue_monitor(ban_queue)
    start_preban_workers(bot, num_workers=2, session_concurrency=3)
    LOGGER.info("Bot is running.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
