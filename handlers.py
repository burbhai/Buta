from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional, Tuple

from pyrogram import Client, filters, types
from pyrogram.handlers import MessageHandler
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
    "help",
    "ping",
    "preban",
    "status",
    "addsession",
    "addsudo",
    "remsudo",
    "verify",
    "set",
    "verify_delay",
    "manage",
    "set_log",
    "set_session",
    "health",
]
COMMAND_PREFIXES = Config.COMMAND_PREFIXES
GROUP_FILTER = filters.group
if hasattr(filters, "supergroup"):
    GROUP_FILTER |= filters.supergroup
ANON_COMMAND_MESSAGE = (
    "⚠️ This command cannot be used anonymously. Please switch to your user account."
)


def command_filter(commands):
    """Build command filter with configured prefixes."""
    return filters.command(commands, prefixes=COMMAND_PREFIXES)


def _has_handler(app: Client, callback_names: set[str]) -> bool:
    """Check whether a handler callback is already registered."""
    for group in app.dispatcher.groups.values():
        for handler in group:
            if isinstance(handler, MessageHandler):
                callback = getattr(handler.callback, "__name__", "")
                if callback in callback_names:
                    return True
    return False


def register_handlers(app: Client) -> None:
    """Register handlers and fallback commands."""
    LOGGER.info("Registering handlers.")
    if not _has_handler(app, {"start"}):
        @app.on_message(filters.command("start") & (filters.private | GROUP_FILTER))
        async def start_handler(client, message):
            await message.reply("✅ Bot is alive.")
    if not _has_handler(app, {"ping_command"}):
        @app.on_message(filters.command("ping") & (filters.private | GROUP_FILTER))
        async def ping_handler(client, message):
            await message.reply("✅ Bot is alive.")
    LOGGER.info("Handlers registered.")

async def _get_session_count() -> int:
    """Return the active session count, falling back safely on errors."""
    try:
        sessions = await get_active_sessions()
    except Exception:
        LOGGER.exception("Failed to fetch active sessions.")
        return 0
    return len(sessions)


async def _safe_reply(message: types.Message, text: str, reply_markup: Optional[types.InlineKeyboardMarkup] = None) -> None:
    """Safely reply to a message with fallback send_message."""
    try:
        await message.reply(text, reply_markup=reply_markup)
    except Exception:
        LOGGER.exception("Failed to reply to message.")
        try:
            await message._client.send_message(message.chat.id, text, reply_markup=reply_markup)
        except Exception:
            LOGGER.exception("Failed to send fallback reply.")


async def _safe_edit(cb: types.CallbackQuery, text: str, reply_markup: Optional[types.InlineKeyboardMarkup] = None) -> None:
    """Safely edit callback messages with fallback reply."""
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
    """Answer a callback query with optional text."""
    try:
        if text is None:
            await cb.answer()
        else:
            await cb.answer(text, show_alert=show_alert)
    except Exception:
        LOGGER.exception("Failed to answer callback query.")


def _log_command_update(message: types.Message) -> None:
    """Log raw command updates for debugging."""
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
    """Reject commands sent via anonymous admin or sender_chat."""
    if message.from_user is None or message.sender_chat is not None:
        await _safe_reply(message, ANON_COMMAND_MESSAGE)
        return True
    return False


def _log_command_invocation(message: types.Message, command: str) -> None:
    """Log who invoked a command."""
    user_id = message.from_user.id if message.from_user else None
    LOGGER.info(
        "Command invoked: %s by user_id=%s chat_id=%s chat_type=%s",
        command,
        user_id,
        message.chat.id,
        message.chat.type,
    )


async def _require_owner(message: types.Message) -> bool:
    """Return True if the sender is an owner; otherwise send a warning."""
    if await _reject_anonymous_command(message):
        return False
    if not message.from_user or message.from_user.id not in Config.OWNERS:
        await _safe_reply(message, "❌ This command is restricted to owners.")
        return False
    return True


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
    """Build the /start keyboard with quick actions and role-specific tools."""
    rows = [
        [
            types.InlineKeyboardButton("❤️ Love", callback_data="love_send"),
            types.InlineKeyboardButton("🆘 Help", callback_data="start_help"),
            types.InlineKeyboardButton("🔁 Ping", callback_data="start_ping"),
        ]
    ]
    if is_owner:
        rows.extend(
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
    elif not has_sudo:
        rows.append([types.InlineKeyboardButton("💳 Payment Options", callback_data="payment_info")])
    return types.InlineKeyboardMarkup(rows)

def _sudo_panel_keyboard() -> types.InlineKeyboardMarkup:
    """Return the sudo panel keyboard."""
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("💌 Send Love", callback_data="love_send")],
            [types.InlineKeyboardButton("🔙 Back", callback_data="home")],
        ]
    )

def _owner_panel_keyboard() -> types.InlineKeyboardMarkup:
    """Return the owner panel keyboard."""
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
    """Return payment helper keyboard."""
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("📤 Send Payment Screenshot", callback_data="payment_how")],
            [types.InlineKeyboardButton("🔙 Back", callback_data="home")],
        ]
    )

def _owner_action_keyboard(action: str) -> types.InlineKeyboardMarkup:
    """Return owner action keyboard for a given action."""
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("🆔 Provide User ID/Username", callback_data=f"{action}_prompt")],
            [types.InlineKeyboardButton("🔙 Back", callback_data="owner_panel")],
        ]
    )


def _help_keyboard() -> types.InlineKeyboardMarkup:
    """Return help shortcut buttons for owners."""
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton("✅ Verify On", callback_data="help_verify_on"),
                types.InlineKeyboardButton("🛑 Verify Off", callback_data="help_verify_off"),
            ],
            [types.InlineKeyboardButton("📥 Manage Sessions", callback_data="help_manage")],
            [types.InlineKeyboardButton("🔙 Back", callback_data="home")],
        ]
    )


def _dm_only_message() -> str:
    """Return a DM-only warning string."""
    return "⚠️ This feature is available in private chat. Please DM the bot."

def _build_help_text(is_owner: bool, has_sudo: bool) -> str:
    """Build help text for /help and inline help callbacks."""
    text = (
        "🆘 **Help Menu**\n\n"
        "Common commands:\n"
        "• /ping - Check if bot is alive\n"
        "• /preban <user_id or @username> - Queue a pre-ban\n"
        "• /status - Queue status\n\n"
    )
    if is_owner:
        text += (
            "Owner commands:\n"
            "• /addsession <session_string>\n"
            "• /addsudo <user_id or @username>\n"
            "• /remsudo <user_id or @username>\n"
            "• /verify <on|off>\n"
            "• /verify_delay <seconds>\n"
            "• /manage - List sessions\n"
            "• /set <key> <value> - Configure defaults\n"
        )
    elif has_sudo:
        text += "You have sudo access. Use **Send Love** to submit targets."
    else:
        text += "You do not have sudo access yet. Submit payment proof to gain access."
    return text

async def _validate_single_session_for_preban(cb: types.CallbackQuery) -> bool:
    """Ensure at least one session exists before starting pre-ban via button."""
    if not cb.from_user:
        return False
    is_owner = cb.from_user.id in Config.OWNERS
    has_sudo = is_owner or await has_access(cb.from_user.id)
    session_count = await _get_session_count()
    if session_count == 0:
        await _answer_cb(cb, "No sessions available.", show_alert=True)
        await _safe_edit(
            cb,
            "❌ No session available for banning. Ask the admin to add one via /set_session in the session group.",
            reply_markup=_start_keyboard(is_owner, has_sudo),
        )
        return False
    return True

@bot.on_message(command_filter(COMMANDS))
async def log_commands(bot, message):
    """Log command traffic for debugging."""
    _log_command_update(message)


@bot.on_message(command_filter(COMMANDS) & GROUP_FILTER)
async def reject_anonymous_group_commands(bot, message):
    """Reject anonymous admin commands in groups."""
    if await _reject_anonymous_command(message):
        return


# BotFather privacy mode must be disabled so the bot can read group commands.


@bot.on_message(
    command_filter(
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
    """Redirect channel command usage to DM."""
    try:
        if await _reject_anonymous_command(message):
            return
        await _safe_reply(message, _dm_only_message())
    except Exception:
        LOGGER.exception("Channel redirect handler failed.")

@bot.on_message(command_filter("ping") & (filters.private | GROUP_FILTER))
async def ping_command(bot, message):
    """Respond to /ping for responsiveness checks."""
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "ping")
        session_count = await _get_session_count()
        await _safe_reply(message, f"✅ Bot is active. Sessions loaded: {session_count}")
    except Exception:
        LOGGER.exception("Ping command failed.")
        await _safe_reply(message, "❌ Failed to respond to ping.")


@bot.on_message(command_filter("help") & (filters.private | GROUP_FILTER))
async def help_command(bot, message):
    """Show help information and shortcuts."""
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "help")
        is_owner = message.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(message.from_user.id)
        text = _build_help_text(is_owner, has_sudo)
        markup = _help_keyboard() if is_owner else None
        await _safe_reply(message, text, reply_markup=markup)
    except Exception:
        LOGGER.exception("Help command failed.")
        await _safe_reply(message, "❌ Failed to load help information.")

@bot.on_message(command_filter("start") & (filters.private | GROUP_FILTER))
async def start(bot, message):
    """Handle /start for onboarding."""
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "start")
        is_owner = message.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(message.from_user.id)
        if message.chat.type == "private":
            intro = (
                "👋 **Welcome!**\n\n"
                "Use /help to see available commands, or tap the buttons below.\n"
                "To send a pre-ban request, use **Send Love** or /preban.\n"
            )
        else:
            intro = "👋 **Welcome!**\n\nPlease DM me for full instructions."
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
            f"{intro}\n{title}\n\n{body}",
            reply_markup=_start_keyboard(is_owner, has_sudo),
        )
    except Exception:
        LOGGER.exception("Start handler failed.")
        await _safe_reply(message, "❌ Something went wrong. Please try again.")

@bot.on_callback_query(filters.regex(r"^start_help$"))
async def start_help(bot, cb):
    """Show help content from the /start quick action."""
    try:
        if not cb.from_user:
            return
        await _answer_cb(cb)
        is_owner = cb.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(cb.from_user.id)
        text = _build_help_text(is_owner, has_sudo)
        markup = _help_keyboard() if is_owner else _start_keyboard(is_owner, has_sudo)
        await _safe_edit(cb, text, reply_markup=markup)
    except Exception:
        LOGGER.exception("Start help callback failed.")
        await _safe_edit(cb, "❌ Failed to load help information.")

@bot.on_callback_query(filters.regex(r"^start_ping$"))
async def start_ping(bot, cb):
    """Respond to ping from inline button."""
    try:
        if not cb.from_user:
            return
        await _answer_cb(cb)
        session_count = await _get_session_count()
        is_owner = cb.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(cb.from_user.id)
        await _safe_edit(
            cb,
            f"✅ Bot is active. Sessions loaded: {session_count}",
            reply_markup=_start_keyboard(is_owner, has_sudo),
        )
    except Exception:
        LOGGER.exception("Start ping callback failed.")
        await _safe_edit(cb, "❌ Failed to respond to ping.")

@bot.on_callback_query(filters.regex(r"^home$") & filters.private)
async def go_home(bot, cb):
    """Return to home screen in callbacks."""
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
    """Show payment info screen."""
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
    """Show payment submission instructions."""
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
    """Start sudo pre-ban flow via button."""
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
        if not await _validate_single_session_for_preban(cb):
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
    """Render owner panel screen."""
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
    """Start add sudo flow."""
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
    """Start remove sudo flow."""
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
    """Prompt for sudo user identifier."""
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
    """Prompt to remove sudo user."""
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
    """Display active sudo users."""
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
    """Display session management list."""
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
    """Set log group from callback."""
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
    """Set session validation group from callback."""
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "⚠️ Use /set_session inside the session manager group to configure session intake.",
            reply_markup=_owner_panel_keyboard(),
        )
        return
    except Exception:
        LOGGER.exception("Owner set session handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


@bot.on_callback_query(filters.regex(r"^help_verify_(on|off)$") & filters.private)
async def help_verify_toggle(bot, cb):
    """Handle verify toggle from help buttons."""
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        mode = cb.matches[0].group(1)
        await update_setting("verify_enabled", mode == "on")
        await _safe_edit(cb, f"✅ Verification mode set to {mode}.", reply_markup=_help_keyboard())
    except Exception:
        LOGGER.exception("Help verify toggle failed.")
        await _safe_edit(cb, "❌ Failed to update verification mode.")


@bot.on_callback_query(filters.regex(r"^help_manage$") & filters.private)
async def help_manage_sessions(bot, cb):
    """Shortcut to manage sessions from help buttons."""
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
        kb.append([types.InlineKeyboardButton("🔙 Back", callback_data="home")])
        await _safe_edit(cb, text, reply_markup=types.InlineKeyboardMarkup(kb))
    except Exception:
        LOGGER.exception("Help manage sessions failed.")
        await _safe_edit(cb, "❌ Failed to load sessions.")

@bot.on_message(command_filter("set_log") & (filters.private | GROUP_FILTER))
async def set_log_group(bot, message):
    """Set log group with /set_log."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "set_log")
        await update_setting("log_group", message.chat.id)
        await _safe_reply(message, "✅ This group is now the **Log Group**.")
    except Exception:
        LOGGER.exception("Set log command failed.")
        await _safe_reply(message, "❌ Failed to set log group.")

@bot.on_message(command_filter("set_session") & (filters.private | GROUP_FILTER))
async def set_session_group(bot, message):
    """Set session group with /set_session."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "set_session")
        if message.chat.type not in {"group", "supergroup"}:
            await _safe_reply(message, "⚠️ Use /set_session inside the session manager group.")
            return
        await update_setting("session_group", message.chat.id)
        await _safe_reply(message, "✅ This group is now the **Session Validation Group**.")
    except Exception:
        LOGGER.exception("Set session command failed.")
        await _safe_reply(message, "❌ Failed to set session group.")

@bot.on_message(command_filter("manage") & (filters.private | GROUP_FILTER))
async def manage_sessions(bot, message):
    """List active sessions via /manage."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "manage")
        all_s = await get_active_sessions()
        text = f"📑 **Active Sessions ({len(all_s)}):**\n\n"
        kb = []
        for s in all_s:
            text += f"👤 {s['name']} ({s['phone']})\n"
            kb.append([types.InlineKeyboardButton(f"Remove {s['phone']}", callback_data=f"rem_{s['phone']}")])

        kb.append([types.InlineKeyboardButton("🆘 Help", callback_data="home")])
        await _safe_reply(message, text, reply_markup=types.InlineKeyboardMarkup(kb))
    except Exception:
        LOGGER.exception("Manage sessions command failed.")
        await _safe_reply(message, "❌ Failed to load sessions.")

@bot.on_callback_query(filters.regex(r"^rem_(.+)$"))
async def remove_session(bot, cb):
    """Remove a session from callback action."""
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
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

@bot.on_message(command_filter("preban") & (filters.private | GROUP_FILTER))
async def preban_user(bot, message):
    """Queue a pre-ban request via /preban."""
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "preban")
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


@bot.on_message(command_filter("status") & (filters.private | GROUP_FILTER))
async def status_command(bot, message):
    """Show queue status with /status."""
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "status")
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


@bot.on_message(command_filter("health") & (filters.private | GROUP_FILTER))
async def health_command(bot, message):
    """Show health snapshot for owners."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "health")
        db_ok = await check_db_health()
        session_count = await _get_session_count()
        queue_snapshot = await get_queue_snapshot()
        worker_status = get_worker_status()
        text = (
            "✅ Database: "
            f"{'OK' if db_ok else 'FAIL'}\n"
            f"📦 Sessions: {session_count}\n"
            f"🔁 Workers Active: {worker_status['alive']}\n"
            f"📈 Queue: {queue_snapshot['queue_length']}"
        )
        await _safe_reply(message, text)
    except Exception:
        LOGGER.exception("Health command failed.")
        await _safe_reply(message, "❌ Failed to collect health status.")


@bot.on_message(command_filter("addsession") & (filters.private | GROUP_FILTER))
async def add_session_command(bot, message):
    """Add a session string to the database."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "addsession")
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
            try:
                await add_session(session_string, me.first_name, me.phone_number or str(me.id))
            except Exception:
                LOGGER.exception("Failed to store session. The database may be unavailable.")
                await _safe_reply(
                    message,
                    "⚠️ Session validated but failed to save. The database may be down.",
                )
                return
            await _safe_reply(message, f"✅ Session added for {me.first_name}.")
        finally:
            if started:
                await temp.stop()
    except Exception:
        LOGGER.exception("Add session command failed.")
        await _safe_reply(message, "❌ Failed to add session.")


@bot.on_message(command_filter("addsudo") & (filters.private | GROUP_FILTER))
async def add_sudo_command(bot, message):
    """Grant sudo access to a user."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "addsudo")
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


@bot.on_message(command_filter("remsudo") & (filters.private | GROUP_FILTER))
async def remove_sudo_command(bot, message):
    """Revoke sudo access from a user."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "remsudo")
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


@bot.on_message(command_filter("verify") & (filters.private | GROUP_FILTER))
async def set_verify_mode(bot, message):
    """Toggle verify mode on or off."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "verify")
        if len(message.command) < 2:
            await _safe_reply(message, "Usage: /verify <on|off>", reply_markup=_help_keyboard())
            return
        mode = message.command[1].lower()
        if mode not in {"on", "off"}:
            await _safe_reply(message, "Usage: /verify <on|off>", reply_markup=_help_keyboard())
            return
        await update_setting("verify_enabled", mode == "on")
        await _safe_reply(message, f"✅ Verification mode set to {mode}.")
    except Exception:
        LOGGER.exception("Verify command failed.")
        await _safe_reply(message, "❌ Failed to update verification mode.")


@bot.on_message(command_filter("verify_delay") & (filters.private | GROUP_FILTER))
async def set_verify_delay(bot, message):
    """Set verification delay for bans."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "verify_delay")
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


@bot.on_message(command_filter("set") & (filters.private | GROUP_FILTER))
async def set_command(bot, message):
    """Update configurable defaults via /set."""
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "set")
        if len(message.command) < 3:
            await _safe_reply(
                message,
                "Usage:\n"
                "/set default_duration <hours>\n"
                "/set approval_text <text>\n"
                "/set approval_durations <comma-separated hours>",
            )
            return
        key = message.command[1].lower()
        value = " ".join(message.command[2:])
        if key == "default_duration":
            try:
                hours = int(value)
            except ValueError:
                await _safe_reply(message, "❌ default_duration must be a number of hours.")
                return
            await update_setting("default_duration", hours)
            await _safe_reply(message, f"✅ Default approval duration set to {hours}h.")
            return
        if key == "approval_text":
            await update_setting("approval_text", value.strip())
            await _safe_reply(message, "✅ Approval text updated.")
            return
        if key == "approval_durations":
            parts = [p.strip() for p in value.split(",") if p.strip()]
            try:
                durations = [int(p) for p in parts]
            except ValueError:
                await _safe_reply(message, "❌ approval_durations must be comma-separated integers.")
                return
            await update_setting("approval_durations", durations)
            await _safe_reply(message, f"✅ Approval durations set to: {', '.join(map(str, durations))}h.")
            return
        await _safe_reply(message, "❌ Unknown setting key. Use /set for usage.")
    except Exception:
        LOGGER.exception("Set command failed.")
        await _safe_reply(message, "❌ Failed to update settings.")
