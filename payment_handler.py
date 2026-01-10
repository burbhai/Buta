# payment_handler.py
from pyrogram import filters
from pyrogram.types import Message
from config import OWNER_IDS
import db
import core
from datetime import datetime, timezone


# ─────────────────────────────────────────────
# PAYMENT HANDLER
# ─────────────────────────────────────────────
def register_payment_handler(app):

    # ─── USER UPLOADS PAYMENT SCREENSHOT ──────
    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client, message: Message):
        uid = message.from_user.id
        username = message.from_user.username or str(uid)

        # Forward to log group for admin verification
        log_group_id = db.get_setting("log_group") or core.LOG_GROUP_ID
        if log_group_id:
            caption = f"💰 Payment request from @{username}\nUser ID: {uid}"
            try:
                await client.send_photo(
                    chat_id=log_group_id,
                    photo=message.photo.file_id,
                    caption=caption
                )
            except Exception:
                pass

        await message.reply(
            "💳 Thanks! Your payment request has been received.\n"
            "An admin will verify it shortly."
        )

    # ─── ADMIN APPROVES PAYMENT ───────────────
    @app.on_message(filters.command("approve") & filters.group)
    async def approve_user(client, message: Message):
        admin_id = message.from_user.id
        if admin_id not in OWNER_IDS:
            await message.reply("❌ You are not allowed to approve payments.")
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

        # Approve payment in DB
        success = db.approve_payment(target_id)
        if not success:
            await message.reply(f"❌ No pending payment found for user {target_id}.")
            return

        # Grant access dynamically based on DB hours or fallback
        user_record = db.users.find_one({"user_id": target_id})
        hours = 6  # fallback default
        if user_record and "expiry" in user_record:
            expiry_time = user_record["expiry"]
            if isinstance(expiry_time, datetime):
                remaining = expiry_time - datetime.now(timezone.utc)
                hours = max(1, int(remaining.total_seconds() // 3600))

        core.grant_access(target_id, hours)

        await message.reply(f"✅ User {target_id} approved. Access granted for {hours}h.")

        # Notify user privately
        try:
            await client.send_message(
                chat_id=target_id,
                text=f"🎉 Your payment has been verified. Access granted for {hours}h!"
            )
        except Exception:
            pass
