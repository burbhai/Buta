from pyrogram import filters
from pyrogram.types import Message
import core
import db
from config import OWNER_IDS

def register_payment_handler(app):

    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client, message: Message):
        uid = message.from_user.id
        log_group_id = db.get_setting("log_group")
        if log_group_id:
            caption = f"💰 Payment request from @{message.from_user.username or uid}"
            await client.send_photo(chat_id=log_group_id, photo=message.photo.file_id, caption=caption)
        await message.reply("💳 Payment received. Admin will verify.")

    @app.on_message(filters.command("approve") & filters.group)
    async def approve_user(client, message: Message):
        admin_id = message.from_user.id
        if admin_id not in OWNER_IDS:
            return
        args = message.text.split()
        if len(args) < 2:
            await message.reply("Usage: /approve <user_id>")
            return
        try:
            target_id = int(args[1])
        except ValueError:
            await message.reply("❌ Invalid user ID.")
            return
        core.grant_access(target_id, 6)  # fixed 6h for now
        await message.reply(f"✅ User {target_id} approved for 6h")
        try:
            await client.send_message(target_id, "🎉 Payment verified. Access granted for 6h!")
        except Exception:
            pass
