import asyncio
from pyrogram import Client, filters
from config import Config
from db import get_settings, add_session, get_active_sessions
from core import pre_ban_worker
import handlers  # noqa: F401
import payment_handler  # noqa: F401

bot = Client("PreBanBot", api_id=Config.API_ID, api_hash=Config.API_HASH, bot_token=Config.BOT_TOKEN, in_memory=True)

@bot.on_message(filters.text & filters.group)
async def auto_session_val(client, message):
    if not message.from_user or not message.text:
        return
    conf = await get_settings()
    if message.chat.id == conf.get("session_group") and message.from_user.id in Config.OWNERS:
        # Assume message.text is the session string
        temp = Client("temp", session_string=message.text, api_id=Config.API_ID, api_hash=Config.API_HASH, in_memory=True)
        started = False
        try:
            await temp.start()
            started = True
            me = await temp.get_me()
            await add_session(message.text, me.first_name, me.phone_number)
            active_sessions = await get_active_sessions()
            await message.reply(
                f"✅ Session Valid: {me.first_name} added.\n"
                f"📊 Active Sessions: {len(active_sessions)}"
            )
        except Exception as e:
            await message.reply(f"❌ Invalid Session: {e}")
        finally:
            if started:
                await temp.stop()

async def main():
    await bot.start()
    asyncio.create_task(pre_ban_worker(bot))
    print("Bot is running...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
