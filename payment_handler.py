from pyrogram import filters
from pyrogram.types import Message
from config import OWNER_IDS
import db
import core


# ─────────────────────────────────────────────
# PAYMENT HANDLER
# ─────────────────────────────────────────────
def register_payment_handler(app):

    # User uploads payment screenshot in private chat
    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client, message: Message):
        uid = message.from_user.id

        if not core.has_active_access(uid):
            await message.reply(
                "💳 Thanks! Your payment request has been received.\n"
                "An admin will verify it shortly."
            )

        # Forward to log group
        log_group_id = db.get_setting("log_group")
        if log_group_id:
            caption = f"💰 Payment request from @{message.from_user.username or uid}\nUser ID: {uid}"
            try:
                await client.send_photo(
                    chat_id=log_group_id,
                    photo=message.photo.file_id,
                    caption=caption
                )
            except Exception:
                pass

        await message.reply("✅ Screenshot forwarded for admin approval.")


    # Admin approves user manually in log group
    @app.on_message(filters.command("approve") & filters.group)
    async def approve_user(client, message: Message):
        uid = message.from_user.id
        chat_id = message.chat.id

        if uid not in OWNER_IDS:
            return

        args = message.text.split()
        if len(args) < 2:
            await message.reply("Usage: `/approve <user_id>`")
            return

        try:
            target_id = int(args[1])
        except ValueError:
            await message.reply("❌ Invalid user ID.")
            return

        # Grant access (default 6h for example)
        db.approve_payment(target_id)
        core.grant_access(target_id, 6)  # Or load from db if dynamic hours

        await message.reply(f"✅ User {target_id} approved and access granted.")

        # Notify user privately
        try:
            await client.send_message(
                chat_id=target_id,
                text="🎉 Your payment has been verified. Access granted!"
            )
        except Exception:
            pass
