from pyrogram import filters, types

from bot_instance import bot
from pyrogram.errors import RPCError
from db import (
    update_setting,
    get_active_sessions,
    deactivate_session,
    has_access,
    give_access,
    revoke_access,
    get_active_sudo_users,
)
from config import Config
from core import ban_queue

LOVE_TRACKER = {}

def _start_keyboard(is_owner: bool, has_sudo: bool) -> types.InlineKeyboardMarkup:
    if is_owner:
        return types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton("👑 Owner Panel", callback_data="owner_panel"),
                ],
                [
                    types.InlineKeyboardButton("📥 Manage Sessions", callback_data="owner_manage_sessions"),
                    types.InlineKeyboardButton("📄 Sudo List", callback_data="owner_sudo_list"),
                ],
                [
                    types.InlineKeyboardButton("📝 Set Log Group", callback_data="owner_set_log"),
                    types.InlineKeyboardButton("🔐 Set Session Group", callback_data="owner_set_session"),
                ],
            ]
        )
    if has_sudo:
        return types.InlineKeyboardMarkup(
            [
                [types.InlineKeyboardButton("💌 Send Love", callback_data="love_send")],
            ]
        )
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("💳 Payment Options", callback_data="payment_info")],
        ]
    )

def _sudo_panel_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("💌 Send Love", callback_data="love_send")],
            [types.InlineKeyboardButton("🔙 Back", callback_data="home")],
        ]
    )

def _owner_panel_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton("➕ Add Sudo", callback_data="owner_add_sudo"),
                types.InlineKeyboardButton("➖ Remove Sudo", callback_data="owner_remove_sudo"),
            ],
            [
                types.InlineKeyboardButton("📄 Sudo List", callback_data="owner_sudo_list"),
                types.InlineKeyboardButton("📥 Manage Sessions", callback_data="owner_manage_sessions"),
            ],
            [
                types.InlineKeyboardButton("📝 Set Log Group", callback_data="owner_set_log"),
                types.InlineKeyboardButton("🔐 Set Session Group", callback_data="owner_set_session"),
            ],
            [types.InlineKeyboardButton("🔙 Back", callback_data="home")],
        ]
    )

def _payment_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("📤 Send Payment Screenshot", callback_data="payment_how")],
            [types.InlineKeyboardButton("🔙 Back", callback_data="home")],
        ]
    )

def _owner_action_keyboard(action: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("🆔 Provide User ID/Username", callback_data=f"{action}_prompt")],
            [types.InlineKeyboardButton("🔙 Back", callback_data="owner_panel")],
        ]
    )

@bot.on_message(filters.command("start") & filters.private)
async def start(bot, message):
    if not message.from_user:
        return
    is_owner = message.from_user.id in Config.OWNERS
    has_sudo = is_owner or await has_access(message.from_user.id)
    if is_owner:
        title = "👑 **Owner Panel**"
        body = (
            "Welcome, Owner! Use the panel below to manage sudo users, sessions, and log groups."
        )
    elif has_sudo:
        title = "💌 **Sudo Access Granted**"
        body = "Tap **Send Love** to start a pre-ban request with a username."
    else:
        title = "💳 **Payment Required**"
        body = (
            "Please complete payment to activate **Send Love** access.\n"
            "After payment, send a screenshot here for approval."
        )
    await message.reply(
        f"{title}\n\n{body}",
        reply_markup=_start_keyboard(is_owner, has_sudo),
    )

@bot.on_callback_query(filters.regex(r"^home$") & filters.private)
async def go_home(bot, cb):
    if not cb.from_user:
        return
    is_owner = cb.from_user.id in Config.OWNERS
    has_sudo = is_owner or await has_access(cb.from_user.id)
    if is_owner:
        title = "👑 **Owner Panel**"
        body = (
            "Welcome, Owner! Use the panel below to manage sudo users, sessions, and log groups."
        )
    elif has_sudo:
        title = "💌 **Sudo Access Granted**"
        body = "Tap **Send Love** to start a pre-ban request with a username."
    else:
        title = "💳 **Payment Required**"
        body = (
            "Please complete payment to activate **Send Love** access.\n"
            "After payment, send a screenshot here for approval."
        )
    await cb.message.edit_text(
        f"{title}\n\n{body}",
        reply_markup=_start_keyboard(is_owner, has_sudo),
    )

@bot.on_callback_query(filters.regex(r"^payment_info$") & filters.private)
async def payment_info(bot, cb):
    await cb.message.edit_text(
        "💳 **Payment to Send Love**\n\n"
        "Please complete payment and send your screenshot in this chat.\n"
        "Our team will review and approve your access.",
        reply_markup=_payment_keyboard(),
    )

@bot.on_callback_query(filters.regex(r"^payment_how$") & filters.private)
async def payment_how(bot, cb):
    await cb.message.edit_text(
        "📤 **Send Payment Screenshot**\n\n"
        "Upload your payment proof image here. We'll verify and activate your access.",
        reply_markup=_payment_keyboard(),
    )

@bot.on_callback_query(filters.regex(r"^love_send$") & filters.private)
async def love_send(bot, cb):
    if not cb.from_user:
        return
    is_owner = cb.from_user.id in Config.OWNERS
    if not is_owner and not await has_access(cb.from_user.id):
        await cb.answer("Payment required to send love.", show_alert=True)
        await cb.message.edit_text(
            "💳 **Payment Required**\n\n"
            "Please complete payment to activate **Send Love** access.",
            reply_markup=_payment_keyboard(),
        )
        return
    LOVE_TRACKER[cb.from_user.id] = "awaiting_target"
    await cb.message.edit_text(
        "💌 **Send Love**\n\n"
        "Please reply with the target **@username** or **user ID**.",
        reply_markup=_sudo_panel_keyboard(),
    )

@bot.on_callback_query(filters.regex(r"^owner_panel$") & filters.private)
async def owner_panel(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    await cb.message.edit_text(
        "👑 **Owner Panel**\n\n"
        "Manage sudo users, sessions, and bot settings below.",
        reply_markup=_owner_panel_keyboard(),
    )

@bot.on_callback_query(filters.regex(r"^owner_add_sudo$") & filters.private)
async def owner_add_sudo(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    await cb.message.edit_text(
        "➕ **Add Sudo User**\n\n"
        "Tap the button below and send the user ID or @username.",
        reply_markup=_owner_action_keyboard("owner_add_sudo"),
    )

@bot.on_callback_query(filters.regex(r"^owner_remove_sudo$") & filters.private)
async def owner_remove_sudo(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    await cb.message.edit_text(
        "➖ **Remove Sudo User**\n\n"
        "Tap the button below and send the user ID or @username.",
        reply_markup=_owner_action_keyboard("owner_remove_sudo"),
    )

@bot.on_callback_query(filters.regex(r"^owner_add_sudo_prompt$") & filters.private)
async def owner_add_prompt(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    LOVE_TRACKER[cb.from_user.id] = "owner_add_sudo"
    await cb.message.edit_text(
        "🆔 **Send User ID or @username** to grant sudo access.",
        reply_markup=_owner_panel_keyboard(),
    )

@bot.on_callback_query(filters.regex(r"^owner_remove_sudo_prompt$") & filters.private)
async def owner_remove_prompt(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    LOVE_TRACKER[cb.from_user.id] = "owner_remove_sudo"
    await cb.message.edit_text(
        "🆔 **Send User ID or @username** to revoke sudo access.",
        reply_markup=_owner_panel_keyboard(),
    )

@bot.on_callback_query(filters.regex(r"^owner_sudo_list$") & filters.private)
async def owner_sudo_list(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    sudo_users = await get_active_sudo_users()
    if not sudo_users:
        text = "📄 **Sudo List**\n\nNo active sudo users."
    else:
        lines = "\n".join(f"• `{u['user_id']}`" for u in sudo_users)
        text = f"📄 **Sudo List**\n\n{lines}"
    await cb.message.edit_text(text, reply_markup=_owner_panel_keyboard())

@bot.on_callback_query(filters.regex(r"^owner_manage_sessions$") & filters.private)
async def owner_manage_sessions(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    all_s = await get_active_sessions()
    text = f"📑 **Active Sessions ({len(all_s)}):**\n\n"
    kb = []
    for s in all_s:
        text += f"👤 {s['name']} ({s['phone']})\n"
        kb.append([types.InlineKeyboardButton(f"Remove {s['phone']}", callback_data=f"rem_{s['phone']}")])
    kb.append([types.InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
    await cb.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(kb))

@bot.on_callback_query(filters.regex(r"^owner_set_log$") & filters.private)
async def owner_set_log_cb(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    await update_setting("log_group", cb.message.chat.id)
    await cb.answer("Log group set to this chat.", show_alert=True)
    await cb.message.edit_text(
        "✅ This chat is now the **Log Group**.",
        reply_markup=_owner_panel_keyboard(),
    )

@bot.on_callback_query(filters.regex(r"^owner_set_session$") & filters.private)
async def owner_set_session_cb(bot, cb):
    if not cb.from_user or cb.from_user.id not in Config.OWNERS:
        await cb.answer("Owner only.", show_alert=True)
        return
    await update_setting("session_group", cb.message.chat.id)
    await cb.answer("Session group set to this chat.", show_alert=True)
    await cb.message.edit_text(
        "✅ This chat is now the **Session Validation Group**.",
        reply_markup=_owner_panel_keyboard(),
    )

@bot.on_message(filters.command("set_log") & filters.user(Config.OWNERS))
async def set_log_group(bot, message):
    await update_setting("log_group", message.chat.id)
    await message.reply("✅ This group is now the **Log Group**.")

@bot.on_message(filters.command("set_session") & filters.user(Config.OWNERS))
async def set_session_group(bot, message):
    await update_setting("session_group", message.chat.id)
    await message.reply("✅ This group is now the **Session Validation Group**.")

@bot.on_message(filters.command("manage") & filters.user(Config.OWNERS))
async def manage_sessions(bot, message):
    all_s = await get_active_sessions()
    text = f"📑 **Active Sessions ({len(all_s)}):**\n\n"
    kb = []
    for s in all_s:
        text += f"👤 {s['name']} ({s['phone']})\n"
        kb.append([types.InlineKeyboardButton(f"Remove {s['phone']}", callback_data=f"rem_{s['phone']}")])
    
    await message.reply(text, reply_markup=types.InlineKeyboardMarkup(kb))

@bot.on_callback_query(filters.regex(r"^rem_(.+)$") & filters.user(Config.OWNERS))
async def remove_session(bot, cb):
    phone = cb.matches[0].group(1)
    await deactivate_session(phone)
    await cb.answer("Session removed.", show_alert=True)
    await cb.edit_message_text(f"✅ Removed session for {phone}.")

@bot.on_message(filters.text & filters.private)
async def handle_text_messages(bot, message):
    if not message.from_user or not message.text:
        return
    state = LOVE_TRACKER.get(message.from_user.id)
    if not state:
        return
    if state in {"owner_add_sudo", "owner_remove_sudo"}:
        if message.from_user.id not in Config.OWNERS:
            return
        raw = message.text.strip().lstrip("@")
        if not raw:
            await message.reply("❌ Please send a valid user ID or @username.")
            return
        user_id = None
        if raw.isdigit():
            user_id = int(raw)
        else:
            try:
                user = await bot.get_users(raw)
                user_id = user.id
            except RPCError:
                await message.reply("❌ Unable to resolve that username.")
                return
        if state == "owner_add_sudo":
            await give_access(user_id, 24 * 365 * 10)
            await message.reply(f"✅ Added `{user_id}` as sudo.", reply_markup=_owner_panel_keyboard())
        else:
            await revoke_access(user_id)
            await message.reply(f"✅ Removed `{user_id}` from sudo.", reply_markup=_owner_panel_keyboard())
        LOVE_TRACKER.pop(message.from_user.id, None)
        return
    if state == "awaiting_target":
        is_owner = message.from_user.id in Config.OWNERS
        if not is_owner and not await has_access(message.from_user.id):
            await message.reply("❌ You are not authorized. Send payment proof to get access.")
            LOVE_TRACKER.pop(message.from_user.id, None)
            return
        raw_target = message.text.strip().lstrip("@")
        if not raw_target:
            await message.reply("❌ Please send a valid user ID or @username.")
            return
        target_id = None
        target_username = None
        if raw_target.isdigit():
            target_id = int(raw_target)
        else:
            try:
                target_user = await bot.get_users(raw_target)
                target_id = target_user.id
            except RPCError:
                target_username = raw_target
        if target_id is None and target_username is None:
            await message.reply("❌ Failed to resolve user.")
            return
        await ban_queue.put(({"id": target_id, "username": target_username}, message.from_user.id))
        queued_label = target_id if target_id is not None else f"@{target_username}"
        await message.reply(
            "🕒 **Love Sent to Queue**\n\n"
            f"Target: `{queued_label}`\n"
            "You'll receive results after processing.",
            reply_markup=_sudo_panel_keyboard(),
        )
        LOVE_TRACKER.pop(message.from_user.id, None)

@bot.on_message(filters.command("preban") & filters.private)
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
    target_id = None
    target_username = None
    if raw_target.isdigit():
        target_id = int(raw_target)
    else:
        try:
            target_user = await bot.get_users(raw_target)
            target_id = target_user.id
        except RPCError:
            target_username = raw_target

    if target_id is None and target_username is None:
        await message.reply("❌ Failed to resolve user.")
        return

    await ban_queue.put(({"id": target_id, "username": target_username}, message.from_user.id))
    queued_label = target_id if target_id is not None else f"@{target_username}"
    await message.reply(f"🕒 Added `{queued_label}` to pre-ban queue.")
