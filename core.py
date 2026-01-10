import asyncio
import threading
import time
from pyrogram import Client
from pyrogram.errors import UserNotParticipant
from config import API_ID, API_HASH, QUEUE_DELAY
from db import sessions, get_setting

queue = asyncio.Queue()
lock = asyncio.Lock()


async def preban_user(username: str, bot):
    session_list = list(sessions.find({"active": True}))
    log_group = get_setting("LOG_GROUP")

    for s in session_list:
        try:
            async with Client(
                name="preban",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=s["session"],
                in_memory=True
            ) as app:

                dialogs = await app.get_dialogs()
                for d in dialogs:
                    if d.chat and d.chat.type in ["group", "supergroup", "channel"]:
                        try:
                            await app.ban_chat_member(d.chat.id, username)
                        except UserNotParticipant:
                            # Telegram supports banning even before join if admin
                            await app.ban_chat_member(d.chat.id, username)

        except Exception as e:
            await bot.send_message(log_group, f"❌ Error with session: `{e}`")


async def queue_worker(bot):
    while True:
        username, user_id = await queue.get()
        async with lock:
            await preban_user(username, bot)
            await bot.send_message(user_id, f"✅ `{username}` pre-banned successfully.")
        queue.task_done()
        await asyncio.sleep(QUEUE_DELAY)


def start_worker(bot):
    loop = asyncio.get_event_loop()
    threading.Thread(
        target=lambda: loop.create_task(queue_worker(bot)),
        daemon=True
    ).start()
