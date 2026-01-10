from pyrogram import filters
from pyrogram.types import Message
from config import OWNER_IDS
import db
import core

# ─────────────────────────────────────────────
# PAYMENT HANDLER
# ─────────────────────────────────────────────
def register_payment_handler(app):

    # ─── USER UPLOADS PAYMENT SCREENSHOT ──────
    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client, message: Message):
        uid = message.from_user.id

        # Forward to log group for admin verification
        log_group_id = core.LOG_GROUP_ID
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

        # Store in DB
        db.create_payment_request(uid, hours=6)  # Default 6h, can customize per plan

        await message.reply(
            "💳 Payment received! Admin will verify it shortly."
        )

    # ─── ADMIN APPROVES PAYMENT ───────────────
    @app.on_message(filters.command("approve") & filters.group)
    async def approve_user(client, message: Message):
        admin_id = message.from_user.id
        if admin_id not in OWNER_IDS:
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

        # Grant access in core (memory) + DB
        db.grant_user_access(target_id, hours=6)  # Default 6h, can customize
        core.grant_access(target_id, 6)

        await message.reply(f"✅ User {target_id} approved. Access granted for 6h.")

        # Notify user privately
        try:
            await client.send_message(
                chat_id=target_id,
                text=f"🎉 Payment verified. Access granted for 6h!"
            )
        except Exception:
            pass
