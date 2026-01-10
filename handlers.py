from pyrogram import Client, filters, types
from database import update_setting, get_active_sessions, sessions
from config import Config

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
