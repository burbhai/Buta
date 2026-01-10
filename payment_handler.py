from pyrogram import Client, filters, types
from database import get_settings, give_access
from config import Config

@Client.on_message(filters.photo & filters.private)
async def handle_payment_screenshot(bot, message):
    conf = await get_settings()
    log_group = conf.get("log_group")
    
    if not log_group:
        return await message.reply("Admin hasn't setup log group yet.")

    # Forward to log group with approval buttons
    kb = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Approve 24h", callback_data=f"app_{message.from_user.id}_24")],
        [types.InlineKeyboardButton("Reject", callback_data=f"rej_{message.from_user.id}")]
    ])
    
    await message.forward(log_group)
    await bot.send_message(log_group, f"💳 **New Payment** from `{message.from_user.id}`", reply_markup=kb)
    await message.reply("🕒 Screenshot sent. Wait for admin approval.")

@Client.on_callback_query(filters.regex(r"app_(\d+)_(\d+)"))
async def approve_user(bot, cb):
    _, uid, hours = cb.data.split("_")
    await give_access(int(uid), int(hours))
    await bot.send_message(int(uid), "✅ **Payment Approved!** You can now send usernames.")
    await cb.answer("User Approved!", show_alert=True)
    await cb.edit_message_text(f"✅ Approved User {uid}")
