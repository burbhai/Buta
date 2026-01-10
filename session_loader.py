from pyrogram import filters
from pyrogram.types import Message
import core
from config import OWNER_IDS

def register_session_handler(app):

    @app.on_message(filters.command("add_session") & filters.group)
    async def add_session(client, message: Message):
        if message.from_user.id not in OWNER_IDS:
            return
        # Example: Owner sends session string
        text = message.text.split(maxsplit=1)
        if len(text) < 2:
            await message.reply("Usage: /add_session <session_string>")
            return
        session_str = text[1].strip()
        # Create new client session (for multi-session)
        new_client = client  # Placeholder, replace with actual Client(session_str)
        core.SESSIONS.append({"id": len(core.SESSIONS)+1, "client": new_client, "active": True})
        await message.reply(f"✅ Session added. Total sessions: {len(core.SESSIONS)}")
