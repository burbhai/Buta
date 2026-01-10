from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
import db
import core
from config import OWNER_IDS

# ─────────────────────────────────────────────
# REGISTER PAYMENT HANDLER
# ─────────────────────────────────────────────
def register_payment_handler(app):

    # ─── USER SENDS PAYMENT SCREENSHOT ─────────
    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client, message: Message):
        uid = message.from_user.id
        plan_hours = getattr(message, "plan_hours", 6)  # default fallback

        # Record in DB
        db.record_payment_request(uid, message.photo.file_id, plan_hours)

        # Forward to log group with Approve button
        log_group_id = db.get_log_group()
        if log_group_id:
            approve_btn = InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{uid}")]]
            )
            try:
                await client.send_photo(
                    chat_id=log_group_id,
                    photo=message.photo.file_id,
                    caption=f"💰 Payment request from @{message.from_user.username or uid}\nUser ID: {uid}\nPlan: {plan_hours}h",
                    reply_markup=approve_btn
                )
            except:
                pass

        await message.reply("💳 Screenshot received! Admin will verify shortly.")

    # ─── ADMIN APPROVE CALLBACK ────────────────
    @app.on_callback_query()
    async def approve_callback(client, cb):
        if not cb.data.startswith("approve:"):
            return

        admin_id = cb.from_user.id
        if admin_id not in OWNER_IDS:
            await cb.answer("❌ Not allowed", show_alert=True)
            return

        target_id = int(cb.data.split(":")[1])
        hours = db.approve_payment(target_id)
        if not hours:
            await cb.answer("❌ No pending payment found", show_alert=True)
            return

        core.grant_access(target_id, hours)

        # Notify user
        try:
            await client.send_message(
                chat_id=target_id,
                text=f"🎉 Payment verified! Access granted for {hours}h."
            )
        except:
            pass

        await cb.message.edit_caption(
            cb.message.caption + f"\n\n✅ Approved by admin {admin_id}"
        )
        await cb.answer("User approved successfully")
