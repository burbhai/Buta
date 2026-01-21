from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional, Tuple

from pyrogram import Client, filters, types
from pyrogram.errors import FloodWait, RPCError

from bot_instance import bot
from config import Config
from core import ban_queue, get_worker_status
from db import (
    add_session,
    deactivate_session,
    get_active_sessions,
    get_active_sudo_users,
    check_db_health,
    give_access,
    has_access,
    revoke_access,
    update_setting,
)
from queue_handler import get_queue_snapshot, get_queue_status

LOVE_TRACKER = {}
LOGGER = logging.getLogger(__name__)
COMMANDS = [
    "start",
    "preban",
    "status",
    "addsession",
    "addsudo",
    "remsudo",
    "verify",
    "verify_delay",
    "manage",
    "set_log",
    "set_session",
    "health",
]
GROUP_FILTER = filters.group
if hasattr(filters, "supergroup"):
    GROUP_FILTER |= filters.supergroup
ANON_COMMAND_MESSAGE = "⚠️ Disable anonymous admin / send command in DM."


async def _safe_reply(message: types.Message, text: str, reply_markup: Optional[types.InlineKeyboardMarkup] = None) -> None:
    try:
        await message.reply(text, reply_markup=reply_markup)
    except Exception:
        LOGGER.exception("Failed to reply to message.")
        try:
            await message._client.send_message(message.chat.id, text, reply_markup=reply_markup)
        except Exception:
            LOGGER.exception("Failed to send fallback reply.")


async def _safe_edit(cb: types.CallbackQuery, text: str, reply_markup: Optional[types.InlineKeyboardMarkup] = None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        LOGGER.exception("Failed to edit callback message.")
        try:
            await cb.message.reply(text, reply_markup=reply_markup)
        except Exception:
            LOGGER.exception("Failed to send fallback reply.")
            try:
                await cb.message._client.send_message(cb.message.chat.id, text, reply_markup=reply_markup)
            except Exception:
                LOGGER.exception("Failed to send fallback fallback reply.")


async def _answer_cb(
    cb: types.CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> None:
    try:
        if text is None:
            await cb.answer()
        else:
            await cb.answer(text, show_alert=show_alert)
    except Exception:
        LOGGER.exception("Failed to answer callback query.")


def _log_command_update(message: types.Message) -> None:
    from_user_id = message.from_user.id if message.from_user else None
    sender_chat_id = message.sender_chat.id if message.sender_chat else None
    text = message.text or message.caption
    LOGGER.info(
        "Command update: chat_id=%s chat_type=%s text=%s from_user_id=%s sender_chat_id=%s",
        message.chat.id,
        message.chat.type,
        text,
        from_user_id,
        sender_chat_id,
    )


async def _reject_anonymous_command(message: types.Message) -> bool:
    if message.from_user is None or message.sender_chat is not None:
        await _safe_reply(message, ANON_COMMAND_MESSAGE)
        return True
    return False


async def _resolve_user_id(client, raw: str) -> Tuple[Optional[int], Optional[str]]:
    """Resolve a user identifier to user_id or fall back to username."""
    raw = raw.strip().lstrip("@")
    if not raw:
        return None, None
    if raw.isdigit():
        return int(raw), None
    try:
        user = await client.get_users(raw)
        return user.id, None
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
        try:
            user = await client.get_users(raw)
            return user.id, None
        except RPCError:
            return None, raw
    except RPCError:
        return None, raw

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


def _dm_only_message() -> str:
    return "⚠️ This feature is available in private chat. Please DM the bot."


@bot.on_message(filters.command(COMMANDS))
async def log_commands(bot, message):
    _log_command_update(message)


@bot.on_message(filters.command(COMMANDS) & GROUP_FILTER)
async def reject_anonymous_group_commands(bot, message):
    if await _reject_anonymous_command(message):
        return


@bot.on_message(
    filters.command(
        [
            "start",
            "preban",
            "status",
            "addsession",
            "addsudo",
            "remsudo",
            "verify",
            "verify_delay",
            "manage",
            "set_log",
            "set_session",
            "health",
        ]
    )
    & filters.channel
)
async def channel_command_redirect(bot, message):
    try:
        if await _reject_anonymous_command(message):
            return
        await _safe_reply(message, _dm_only_message())
    except Exception:
        LOGGER.exception("Channel redirect handler failed.")

@bot.on_message(filters.command("start") & (filters.private | GROUP_FILTER))
async def start(bot, message):
    try:
        if await _reject_anonymous_command(message):
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
        await _safe_reply(
            message,
            f"{title}\n\n{body}",
            reply_markup=_start_keyboard(is_owner, has_sudo),
        )
    except Exception:
        LOGGER.exception("Start handler failed.")
        await _safe_reply(message, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^home$") & filters.private)
async def go_home(bot, cb):
    try:
        if not cb.from_user:
            return
        await _answer_cb(cb)
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
        await _safe_edit(
            cb,
            f"{title}\n\n{body}",
            reply_markup=_start_keyboard(is_owner, has_sudo),
        )
    except Exception:
        LOGGER.exception("Home handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^payment_info$") & filters.private)
async def payment_info(bot, cb):
    try:
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "💳 **Payment to Send Love**\n\n"
            "Please complete payment and send your screenshot in this chat.\n"
            "Our team will review and approve your access.",
            reply_markup=_payment_keyboard(),
        )
    except Exception:
        LOGGER.exception("Payment info handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^payment_how$") & filters.private)
async def payment_how(bot, cb):
    try:
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "📤 **Send Payment Screenshot**\n\n"
            "Upload your payment proof image here. We'll verify and activate your access.",
            reply_markup=_payment_keyboard(),
        )
    except Exception:
        LOGGER.exception("Payment how handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^love_send$") & filters.private)
async def love_send(bot, cb):
    try:
        if not cb.from_user:
            return
        is_owner = cb.from_user.id in Config.OWNERS
        if not is_owner and not await has_access(cb.from_user.id):
            await _answer_cb(cb, "Payment required to send love.", show_alert=True)
            await _safe_edit(
                cb,
                "💳 **Payment Required**\n\n"
                "Please complete payment to activate **Send Love** access.",
                reply_markup=_payment_keyboard(),
            )
            return
        await _answer_cb(cb)
        LOVE_TRACKER[cb.from_user.id] = "awaiting_target"
        await _safe_edit(
            cb,
            "💌 **Send Love**\n\n"
            "Please reply with the target **@username** or **user ID**.",
            reply_markup=_sudo_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Love send handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_panel$") & filters.private)
async def owner_panel(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "👑 **Owner Panel**\n\n"
            "Manage sudo users, sessions, and bot settings below.",
            reply_markup=_owner_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner panel handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_add_sudo$") & filters.private)
async def owner_add_sudo(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "➕ **Add Sudo User**\n\n"
            "Tap the button below and send the user ID or @username.",
            reply_markup=_owner_action_keyboard("owner_add_sudo"),
        )
    except Exception:
        LOGGER.exception("Owner add sudo handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_remove_sudo$") & filters.private)
async def owner_remove_sudo(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "➖ **Remove Sudo User**\n\n"
            "Tap the button below and send the user ID or @username.",
            reply_markup=_owner_action_keyboard("owner_remove_sudo"),
        )
    except Exception:
        LOGGER.exception("Owner remove sudo handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_add_sudo_prompt$") & filters.private)
async def owner_add_prompt(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        LOVE_TRACKER[cb.from_user.id] = "owner_add_sudo"
        await _safe_edit(
            cb,
            "🆔 **Send User ID or @username** to grant sudo access.",
            reply_markup=_owner_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner add prompt handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_remove_sudo_prompt$") & filters.private)
async def owner_remove_prompt(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        LOVE_TRACKER[cb.from_user.id] = "owner_remove_sudo"
        await _safe_edit(
            cb,
            "🆔 **Send User ID or @username** to revoke sudo access.",
            reply_markup=_owner_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner remove prompt handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_sudo_list$") & filters.private)
async def owner_sudo_list(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        sudo_users = await get_active_sudo_users()
        if not sudo_users:
            text = "📄 **Sudo List**\n\nNo active sudo users."
        else:
            lines = "\n".join(f"• `{u['user_id']}`" for u in sudo_users)
            text = f"📄 **Sudo List**\n\n{lines}"
        await _safe_edit(cb, text, reply_markup=_owner_panel_keyboard())
    except Exception:
        LOGGER.exception("Owner sudo list handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_manage_sessions$") & filters.private)
async def owner_manage_sessions(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        all_s = await get_active_sessions()
        text = f"📑 **Active Sessions ({len(all_s)}):**\n\n"
        kb = []
        for s in all_s:
            text += f"👤 {s['name']} ({s['phone']})\n"
            kb.append([types.InlineKeyboardButton(f"Remove {s['phone']}", callback_data=f"rem_{s['phone']}")])
        kb.append([types.InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
        await _safe_edit(cb, text, reply_markup=types.InlineKeyboardMarkup(kb))
    except Exception:
        LOGGER.exception("Owner manage sessions handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_set_log$") & filters.private)
async def owner_set_log_cb(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        await update_setting("log_group", cb.message.chat.id)
        await _safe_edit(
            cb,
            "✅ This chat is now the **Log Group**.",
            reply_markup=_owner_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner set log handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^owner_set_session$") & filters.private)
async def owner_set_session_cb(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        await update_setting("session_group", cb.message.chat.id)
        await _safe_edit(
            cb,
            "✅ This chat is now the **Session Validation Group**.",
            reply_markup=_owner_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner set session handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")

@bot.on_message(filters.command("set_log") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def set_log_group(bot, message):
    try:
        await update_setting("log_group", message.chat.id)
        await _safe_reply(message, "✅ This group is now the **Log Group**.")
    except Exception:
        LOGGER.exception("Set log command failed.")
        await _safe_reply(message, "❌ Failed to set log group.")

@bot.on_message(filters.command("set_session") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def set_session_group(bot, message):
    try:
        await update_setting("session_group", message.chat.id)
        await _safe_reply(message, "✅ This group is now the **Session Validation Group**.")
    except Exception:
        LOGGER.exception("Set session command failed.")
        await _safe_reply(message, "❌ Failed to set session group.")

@bot.on_message(filters.command("manage") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def manage_sessions(bot, message):
    try:
        all_s = await get_active_sessions()
        text = f"📑 **Active Sessions ({len(all_s)}):**\n\n"
        kb = []
        for s in all_s:
            text += f"👤 {s['name']} ({s['phone']})\n"
            kb.append([types.InlineKeyboardButton(f"Remove {s['phone']}", callback_data=f"rem_{s['phone']}")])

        await _safe_reply(message, text, reply_markup=types.InlineKeyboardMarkup(kb))
    except Exception:
        LOGGER.exception("Manage sessions command failed.")
        await _safe_reply(message, "❌ Failed to load sessions.")

@bot.on_callback_query(filters.regex(r"^rem_(.+)$") & filters.user(Config.OWNERS))
async def remove_session(bot, cb):
    try:
        await _answer_cb(cb)
        phone = cb.matches[0].group(1)
        await deactivate_session(phone)
        await _safe_edit(cb, f"✅ Removed session for {phone}.")
    except Exception:
        LOGGER.exception("Remove session callback failed.")
        await _safe_edit(cb, "❌ Failed to remove session.")

@bot.on_message(filters.text & filters.private)
async def handle_text_messages(bot, message):
    try:
        if not message.from_user or not message.text:
            return
        state = LOVE_TRACKER.get(message.from_user.id)
        if not state:
            return
        if state in {"owner_add_sudo", "owner_remove_sudo"}:
            if message.from_user.id not in Config.OWNERS:
                return
            user_id, username = await _resolve_user_id(bot, message.text)
            if user_id is None and username is None:
                await _safe_reply(message, "❌ Please send a valid user ID or @username.")
                return
            if user_id is None and username is not None:
                await _safe_reply(message, "❌ Unable to resolve that username.")
                return
            if state == "owner_add_sudo":
                await give_access(user_id, 24 * 365 * 10)
                await _safe_reply(message, f"✅ Added `{user_id}` as sudo.", reply_markup=_owner_panel_keyboard())
            else:
                await revoke_access(user_id)
                await _safe_reply(message, f"✅ Removed `{user_id}` from sudo.", reply_markup=_owner_panel_keyboard())
            LOVE_TRACKER.pop(message.from_user.id, None)
            return
        if state == "awaiting_target":
            is_owner = message.from_user.id in Config.OWNERS
            if not is_owner and not await has_access(message.from_user.id):
                await _safe_reply(message, "❌ You are not authorized. Send payment proof to get access.")
                LOVE_TRACKER.pop(message.from_user.id, None)
                return
            target_id, target_username = await _resolve_user_id(bot, message.text)
            if target_id is None and target_username is None:
                await _safe_reply(message, "❌ Failed to resolve user.")
                return
            if Config.QUEUE_MAXSIZE > 0 and ban_queue.full():
                await _safe_reply(message, "⚠️ Queue is full. Please try again in a moment.")
                LOVE_TRACKER.pop(message.from_user.id, None)
                return
            await ban_queue.put(({"id": target_id, "username": target_username}, message.from_user.id))
            queued_label = target_id if target_id is not None else f"@{target_username}"
            await _safe_reply(
                message,
                "🕒 **Love Sent to Queue**\n\n"
                f"Target: `{queued_label}`\n"
                "You'll receive results after processing.",
                reply_markup=_sudo_panel_keyboard(),
            )
            LOVE_TRACKER.pop(message.from_user.id, None)
    except Exception:
        LOGGER.exception("Handle text handler failed.")
        await _safe_reply(message, "❌ Something went wrong. Please try again.")

@bot.on_message(filters.command("preban") & (filters.private | GROUP_FILTER))
async def preban_user(bot, message):
    try:
        if await _reject_anonymous_command(message):
            return

        is_owner = message.from_user.id in Config.OWNERS
        if not is_owner and not await has_access(message.from_user.id):
            await _safe_reply(message, "❌ You are not authorized. Send payment proof to get access.")
            return

        if len(message.command) < 2:
            await _safe_reply(message, "Usage: /preban <user_id or @username>")
            return

        target_id, target_username = await _resolve_user_id(bot, message.command[1])

        if target_id is None and target_username is None:
            await _safe_reply(message, "❌ Failed to resolve user.")
            return

        if Config.QUEUE_MAXSIZE > 0 and ban_queue.full():
            await _safe_reply(message, "⚠️ Queue is full. Please try again in a moment.")
            return

        await ban_queue.put(({"id": target_id, "username": target_username}, message.from_user.id))
        queued_label = target_id if target_id is not None else f"@{target_username}"
        await _safe_reply(message, f"🕒 Added `{queued_label}` to pre-ban queue.")
    except Exception:
        LOGGER.exception("Preban command failed.")
        await _safe_reply(message, "❌ Failed to queue pre-ban request.")


@bot.on_message(filters.command("status") & (filters.private | GROUP_FILTER))
async def status_command(bot, message):
    try:
        if await _reject_anonymous_command(message):
            return
        is_owner = message.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(message.from_user.id)
        if not has_sudo:
            await _safe_reply(message, "❌ You are not authorized to view status.")
            return
        status_text = await get_queue_status()
        await _safe_reply(message, status_text)
    except Exception:
        LOGGER.exception("Status command failed.")
        await _safe_reply(message, "❌ Failed to get queue status.")


@bot.on_message(filters.command("health") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def health_command(bot, message):
    try:
        db_ok = await check_db_health()
        sessions = await get_active_sessions()
        queue_snapshot = await get_queue_snapshot()
        worker_status = get_worker_status()
        text = (
            "🩺 **Health Check**\n\n"
            f"DB: {'OK' if db_ok else 'FAIL'}\n"
            f"Active Sessions: {len(sessions)}\n"
            f"Workers: {worker_status['alive']}/{worker_status['total']}\n"
            f"Queue Length: {queue_snapshot['queue_length']}\n"
            f"Active Tasks: {queue_snapshot['active_tasks']}\n"
            f"Total Completed: {queue_snapshot['success_count']}"
        )
        await _safe_reply(message, text)
    except Exception:
        LOGGER.exception("Health command failed.")
        await _safe_reply(message, "❌ Failed to collect health status.")


@bot.on_message(filters.command("addsession") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def add_session_command(bot, message):
    try:
        if len(message.command) < 2:
            await _safe_reply(message, "Usage: /addsession <session_string>")
            return
        session_string = message.command[1]
        temp = Client(
            f"session_add_{uuid.uuid4().hex}",
            session_string=session_string,
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
        )
        started = False
        try:
            await temp.start()
            started = True
            me = await temp.get_me()
            await add_session(session_string, me.first_name, me.phone_number or str(me.id))
            await _safe_reply(message, f"✅ Session added for {me.first_name}.")
        finally:
            if started:
                await temp.stop()
    except Exception:
        LOGGER.exception("Add session command failed.")
        await _safe_reply(message, "❌ Failed to add session.")


@bot.on_message(filters.command("addsudo") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def add_sudo_command(bot, message):
    try:
        if len(message.command) < 2:
            await _safe_reply(message, "Usage: /addsudo <user_id or @username>")
            return
        user_id, username = await _resolve_user_id(bot, message.command[1])
        if user_id is None:
            await _safe_reply(message, f"❌ Failed to resolve {username or 'user'}.")
            return
        await give_access(user_id, 24 * 365 * 10)
        await _safe_reply(message, f"✅ Added `{user_id}` as sudo.")
    except Exception:
        LOGGER.exception("Add sudo command failed.")
        await _safe_reply(message, "❌ Failed to add sudo user.")


@bot.on_message(filters.command("remsudo") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def remove_sudo_command(bot, message):
    try:
        if len(message.command) < 2:
            await _safe_reply(message, "Usage: /remsudo <user_id or @username>")
            return
        user_id, username = await _resolve_user_id(bot, message.command[1])
        if user_id is None:
            await _safe_reply(message, f"❌ Failed to resolve {username or 'user'}.")
            return
        await revoke_access(user_id)
        await _safe_reply(message, f"✅ Removed `{user_id}` from sudo.")
    except Exception:
        LOGGER.exception("Remove sudo command failed.")
        await _safe_reply(message, "❌ Failed to remove sudo user.")


@bot.on_message(filters.command("verify") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def set_verify_mode(bot, message):
    try:
        if len(message.command) < 2:
            await _safe_reply(message, "Usage: /verify <on|off>")
            return
        mode = message.command[1].lower()
        if mode not in {"on", "off"}:
            await _safe_reply(message, "Usage: /verify <on|off>")
            return
        await update_setting("verify_enabled", mode == "on")
        await _safe_reply(message, f"✅ Verification mode set to {mode}.")
    except Exception:
        LOGGER.exception("Verify command failed.")
        await _safe_reply(message, "❌ Failed to update verification mode.")


@bot.on_message(filters.command("verify_delay") & filters.user(Config.OWNERS) & (filters.private | GROUP_FILTER))
async def set_verify_delay(bot, message):
    try:
        if len(message.command) < 2:
            await _safe_reply(message, "Usage: /verify_delay <seconds>")
            return
        try:
            delay = float(message.command[1])
        except ValueError:
            await _safe_reply(message, "❌ Please provide a numeric delay in seconds.")
            return
        if delay < 0:
            await _safe_reply(message, "❌ Delay must be non-negative.")
            return
        await update_setting("verify_delay", delay)
        await _safe_reply(message, f"✅ Verification delay set to {delay:.2f}s.")
    except Exception:
        LOGGER.exception("Verify delay command failed.")
        await _safe_reply(message, "❌ Failed to update verification delay.")
