from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import db
import core
from config import OWNER_IDS

def register_payment_handler(app):

    # USER SENDS PAYMENT SCREENSHOT
    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client, message: Message):
        uid = message.from_user.id
        log_group_id = db.get_setting("log_group")
        if log_group_id:
            caption = f"💰 Payment request from @{message.from_user.username or uid}\nUser ID: {uid}"
            await client.send_photo(chat_id=log_group_id, photo=message.photo.file_id, caption=caption)
            # Add Approve button
            approve_btn = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{uid}")]])
            await client.send_message(chat_id=log_group_id, text="Approve user:", reply_markup=approve_btn)
        await message.reply("💳 Payment received. Admin will verify.")

    # ADMIN APPROVES VIA BUTTON
    @app.on_callback_query(filters.regex(r"^approve:\d+$"))
    async def approve_callback(client, cb):
        admin_id = cb.from_user.id
        if admin_id not in OWNER_IDS:
            await cb.answer("Not allowed", show_alert=True)
            return
        uid = int(cb.data.split(":")[1])
        success = db.approve_payment(uid)
        if not success:
            await cb.answer("No pending payment for user", show_alert=True)
            return
        # Grant access in core
        expiry = db.get_user_expiry(uid)
        remaining_hours = max(1, int((expiry - db.datetime.utcnow()).total_seconds() // 3600))
        core.grant_access(uid, remaining_hours)
        await cb.message.edit_text(f"✅ User {uid} approved. Access granted for {remaining_hours}h")
        # Notify user privately
        try:
            await client.send_message(uid, f"🎉 Your payment verified. Access granted for {remaining_hours}h!")
        except Exception:
            pass
        await cb.answer("User approved")
