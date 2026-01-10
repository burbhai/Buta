from pyrogram import filters, Client
from pyrogram.types import Message
from pyrogram.errors import SessionPasswordNeeded
from config import OWNER_IDS
import core
import asyncio

def register_session_handler(app: Client):
    """
    Owner sends a Pyrogram session string in the SESSION_GROUP.
    Bot auto-validates and saves it for pre-ban tasks.
    """

    @app.on_message(filters.group & filters.text)
    async def add_session(client: Client, message: Message):
        # Only owner can send sessions
        if message.from_user.id not in OWNER_IDS:
            return

        # Only session group messages
        if message.chat.id != core.SESSION_GROUP_ID:
            return

        session_string = message.text.strip()
        if not session_string:
            return

        # Try to create a temporary Pyrogram Client to validate
        try:
            temp_app = Client(
                name="temp_session",
                session_string=session_string,
                api_id=int(client.api_id),
                api_hash=client.api_hash,
            )
            await temp_app.start()
            me = await temp_app.get_me()  # validate login
            await temp_app.stop()

        except SessionPasswordNeeded:
            await message.reply(
                f"❌ Session requires 2FA password. Please send a valid Pyrogram session without password."
            )
            return

        except Exception as e:
            await message.reply(
                f"❌ Invalid Pyrogram session string.\nError: {e}"
            )
            return

        # Save the valid session
        core.SESSIONS.append({
            "id": len(core.SESSIONS)+1,
            "active": True,
            "client": None,       # will use session_string later to create Client
            "string": session_string,
            "owner_id": message.from_user.id
        })

        await message.reply(
            f"✅ Session validated and saved successfully.\nTotal sessions: {len(core.SESSIONS)}\nUser: @{me.username or me.first_name}"
        )
