from pyrogram import Client, filters, types
from pyrogram.errors import RPCError
from db import update_setting, get_active_sessions, deactivate_session, has_access
from config import Config
from core import ban_queue

@Client.on_message(filters.command("set_log") & filters.user(Config.OWNERS))
async def set_log_group(bot, message):
    await update_setting("log_group", message.chat.id)
    await message.reply("✅ This group is now the **Log Group**.")

@Client.on_message(filters.command("set_session") & filters.user(Config.OWNERS))
async def set_session_group(bot, message):
    await update_setting("session_group", message.chat.id)
    await message.reply("✅ This group is now the **Session Validation Group**.")

@Client.on_message(filters.command("manage") & filters.user(Config.OWNERS))
async def manage_sessions(bot, message):
    all_s = await get_active_sessions()
    text = f"📑 **Active Sessions ({len(all_s)}):**\n\n"
    kb = []
    for s in all_s:
        text += f"👤 {s['name']} ({s['phone']})\n"
        kb.append([types.InlineKeyboardButton(f"Remove {s['phone']}", callback_data=f"rem_{s['phone']}")])
    
    await message.reply(text, reply_markup=types.InlineKeyboardMarkup(kb))

@Client.on_callback_query(filters.regex(r"^rem_(.+)$") & filters.user(Config.OWNERS))
async def remove_session(bot, cb):
    phone = cb.matches[0].group(1)
    await deactivate_session(phone)
    await cb.answer("Session removed.", show_alert=True)
    await cb.edit_message_text(f"✅ Removed session for {phone}.")

@Client.on_message(filters.command("preban") & filters.private)
async def preban_user(bot, message):
    if not message.from_user:
        return

    is_owner = message.from_user.id in Config.OWNERS
    if not is_owner and not await has_access(message.from_user.id):
        await message.reply("❌ You are not authorized. Send payment proof to get access.")
        return

    if len(message.command) < 2:
        await message.reply("Usage: /preban <user_id or @username>")
        return

    raw_target = message.command[1].lstrip("@")
    try:
        if raw_target.isdigit():
            target_user = await bot.get_users(int(raw_target))
        else:
            target_user = await bot.get_users(raw_target)
    except RPCError as exc:
        await message.reply(f"❌ Failed to resolve user: {exc}")
        return

    await ban_queue.put((target_user.id, message.from_user.id))
    await message.reply(f"🕒 Added `{target_user.id}` to pre-ban queue.")
