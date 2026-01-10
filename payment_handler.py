from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import OWNER_IDS
import core
import db

def register_payment_handler(app):

    # ─── User uploads screenshot ───────────────
    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client, message: Message):
        uid = message.from_user.id

        log_group_id = db.get_setting("log_group") or core.LOG_GROUP_ID
        if log_group_id:
            caption = f"💰 Payment request from @{message.from_user.username or uid}\nUser ID: {uid}"
            approve_button = InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{uid}")]]
            )
            try:
                await client.send_photo(
                    chat_id=log_group_id,
                    photo=message.photo.file_id,
                    caption=caption,
                    reply_markup=approve_button
                )
            except Exception:
                pass

        await message.reply(
            "💳 Payment received! Admin will verify shortly."
        )

    # ─── Admin approves via button ─────────────
    @app.on_callback_query(filters.regex(r"^approve:(\d+)$"))
    async def approve_payment_cb(client, cb):
        admin_id = cb.from_user.id
        if admin_id not in OWNER_IDS:
            await cb.answer("❌ Not allowed", show_alert=True)
            return

        target_id = int(cb.data.split(":")[1])

        # Grant access (default 6h)
        core.grant_access(target_id, 6)

        # Optional: mark in DB
        db.mark_payment_approved(target_id, 6)

        # Update log group message
        await cb.message.edit_caption(
            f"✅ Payment approved by @{cb.from_user.username or admin_id}\n"
            f"Access granted: 6h"
        )
        await cb.answer("User approved!")

        # Notify user privately
        try:
            await client.send_message(
                chat_id=target_id,
                text="🎉 Your payment has been verified. Access granted for 6h!"
            )
        except Exception:
            pass
