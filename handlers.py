from __future__ import annotations

# REPORT:
# - Sudo /start shows a Control Panel with 💌 Send Love and ❓ Help only.
# - Owner /start goes directly to a Control Panel with ➕ Add Sudo, 📥 Manage Sessions,
#   📝 Set Log Group, 🔐 Set Session Intake Group, 💌 Send Love, and ❓ Help (two per row).
# - Normal users /start shows payment-only buttons (Payment Plans + Send Payment Proof).
# - Callbacks used: buta:help, buta:back, buta:love:send, buta:owner:add_sudo,
#   buta:owner:manage_sessions, buta:owner:set_log, buta:owner:set_session,
#   buta:payment:info, buta:payment:how, buta:session:remove:* (registered in
#   register_ui_and_commands).
# - Bugs fixed: private-chat detection (enums.ChatType.PRIVATE), callback mismatches,
#   and shared command/callback flows for owner actions.

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from pyrogram import Client, StopPropagation, enums, filters, types
from pyrogram.errors import FloodWait, RPCError
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from config import Config
from core import ban_queue, get_worker_status
from db import (
    add_session,
    check_db_health,
    deactivate_session,
    get_active_sessions,
    get_active_sudo_users,
    get_settings,
    give_access,
    has_access,
    revoke_access,
    update_setting,
)
from queue_handler import get_queue_snapshot, get_queue_status, register_request_event

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
    "cancel",
]
COMMAND_PREFIXES = Config.COMMAND_PREFIXES
GROUP_FILTER = filters.group
if hasattr(filters, "supergroup"):
    GROUP_FILTER |= filters.supergroup


def _message_private_filter(_: Client, message: types.Message) -> bool:
    return bool(getattr(message, "chat", None) and message.chat.type == enums.ChatType.PRIVATE)


def _message_group_filter(_: Client, message: types.Message) -> bool:
    return bool(
        getattr(message, "chat", None)
        and message.chat.type in {enums.ChatType.GROUP, enums.ChatType.SUPERGROUP}
    )


MESSAGE_PRIVATE_FILTER = filters.create(_message_private_filter)
MESSAGE_GROUP_FILTER = filters.create(_message_group_filter)
MESSAGE_PRIVATE_OR_GROUP_FILTER = MESSAGE_PRIVATE_FILTER | MESSAGE_GROUP_FILTER
ANON_COMMAND_MESSAGE = (
    "⚠️ This command cannot be used anonymously. Please switch to your user account."
)

CALLBACK_PREFIX = "buta:"


@dataclass
class LoveState:
    state: str
    updated_at: float


LOVE_TRACKER: dict[int, LoveState] = {}
LOVE_TRACKER_TTL_SECONDS = 120
REMOVE_SESSION_TOKENS: dict[str, str] = {}


# -----------------------------
# Utilities
# -----------------------------

def _extract_command(text: str | None) -> Optional[str]:
    """Extract a command name from text using configured prefixes."""
    if not text:
        return None
    stripped = text.strip()
    for prefix in COMMAND_PREFIXES:
        if stripped.startswith(prefix):
            payload = stripped[len(prefix) :]
            if not payload:
                return None
            command = payload.split(maxsplit=1)[0]
            return command.split("@")[0].lower()
    return None


def command_filter(commands):
    """Build command filter with configured prefixes."""
    return filters.command(commands, prefixes=COMMAND_PREFIXES)


def _cleanup_love_tracker(now: Optional[float] = None) -> None:
    current_time = time.time() if now is None else now
    expired = [
        user_id
        for user_id, entry in LOVE_TRACKER.items()
        if current_time - entry.updated_at > LOVE_TRACKER_TTL_SECONDS
    ]
    for user_id in expired:
        LOVE_TRACKER.pop(user_id, None)


def _set_love_state(user_id: int, state: str) -> None:
    _cleanup_love_tracker()
    LOVE_TRACKER[user_id] = LoveState(state=state, updated_at=time.time())


def _get_love_state(user_id: int) -> Optional[str]:
    _cleanup_love_tracker()
    entry = LOVE_TRACKER.get(user_id)
    if not entry:
        return None
    if time.time() - entry.updated_at > LOVE_TRACKER_TTL_SECONDS:
        LOVE_TRACKER.pop(user_id, None)
        return None
    LOVE_TRACKER[user_id] = LoveState(state=entry.state, updated_at=time.time())
    return entry.state


def _clear_love_state(user_id: int) -> None:
    LOVE_TRACKER.pop(user_id, None)


async def _get_session_count() -> int:
    try:
        sessions = await get_active_sessions()
    except Exception:
        LOGGER.exception("Failed to fetch active sessions.")
        return 0
    return len(sessions)


async def _safe_reply(
    message: types.Message,
    text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> None:
    try:
        await message.reply(text, reply_markup=reply_markup)
        setattr(message, "_buta_replied", True)
    except Exception:
        LOGGER.exception("Failed to reply to message.")
        try:
            await message._client.send_message(message.chat.id, text, reply_markup=reply_markup)
            setattr(message, "_buta_replied", True)
        except Exception:
            LOGGER.exception("Failed to send fallback reply.")


async def _safe_reply_message(
    message: types.Message,
    text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> Optional[types.Message]:
    try:
        reply = await message.reply(text, reply_markup=reply_markup)
        setattr(message, "_buta_replied", True)
        return reply
    except Exception:
        LOGGER.exception("Failed to reply to message.")
        try:
            reply = await message._client.send_message(message.chat.id, text, reply_markup=reply_markup)
            setattr(message, "_buta_replied", True)
            return reply
        except Exception:
            LOGGER.exception("Failed to send fallback reply.")
            return None


async def _safe_edit(
    cb: types.CallbackQuery,
    text: str,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> None:
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


def _format_duration(hours: int) -> str:
    if hours % 24 == 0:
        days = hours // 24
        return f"{days} day" if days == 1 else f"{days} days"
    return f"{hours} hours"


def _sanitize_durations(raw: object) -> list[int]:
    defaults = [24, 72, 168]
    if isinstance(raw, list):
        durations = []
        for item in raw:
            try:
                val = int(item)
            except (TypeError, ValueError):
                continue
            if val > 0:
                durations.append(val)
        return durations or defaults
    return defaults


def _parse_payment_rates(raw: object) -> dict[int, str]:
    rates: dict[int, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                hours = int(key)
            except (TypeError, ValueError):
                continue
            if value is None:
                continue
            rates[hours] = str(value)
        return rates
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                hours = item.get("hours") or item.get("duration") or item.get("h")
                price = item.get("price") or item.get("amount") or item.get("rate")
                try:
                    hours_int = int(hours)
                except (TypeError, ValueError):
                    continue
                if price is not None:
                    rates[hours_int] = str(price)
            elif isinstance(item, str):
                if ":" in item:
                    hours_str, price = item.split(":", 1)
                elif "=" in item:
                    hours_str, price = item.split("=", 1)
                else:
                    continue
                try:
                    hours_int = int(hours_str.strip())
                except ValueError:
                    continue
                rates[hours_int] = price.strip()
    return rates


async def _build_payment_list_text() -> str:
    conf = await get_settings()
    durations = _sanitize_durations(conf.get("approval_durations"))
    rates = _parse_payment_rates(conf.get("payment_rates"))
    default_rates = {24: "₹100", 72: "₹250", 168: "₹500"}
    lines = []
    for hours in durations:
        price = rates.get(hours) or default_rates.get(hours) or "Contact admin"
        lines.append(f"• {_format_duration(hours)} — **{price}**")
    return "\n".join(lines)


async def _start_queue_live_updates(
    client: Client,
    chat_id: int,
    message_id: int,
    request_id: str,
    target_label: str,
    event: Optional[asyncio.Event] = None,
) -> None:
    queue_event = event or register_request_event(request_id)
    started_at = time.time()
    last_text: Optional[str] = None
    while True:
        if queue_event.is_set():
            text = (
                "🚀 **Pre-ban started**\n"
                f"Target: `{target_label}`\n\n"
                "Live queue updates complete. You'll receive results once finished."
            )
            if text != last_text:
                try:
                    await client.edit_message_text(chat_id, message_id, text)
                except Exception:
                    LOGGER.exception("Failed to update queue start message.")
            return
        status_text = await get_queue_status()
        text = (
            "🕒 **Pre-ban queued**\n"
            f"Target: `{target_label}`\n\n"
            f"{status_text}\n\n"
            "We'll keep updating this message until processing starts."
        )
        if text != last_text:
            try:
                await client.edit_message_text(chat_id, message_id, text)
                last_text = text
            except Exception:
                LOGGER.exception("Failed to update queue status message.")
        if time.time() - started_at > 300:
            return
        await asyncio.sleep(3)


async def _reject_anonymous_command(message: types.Message) -> bool:
    if message.from_user is None or message.sender_chat is not None:
        await _safe_reply(message, ANON_COMMAND_MESSAGE)
        return True
    return False


async def _require_owner(message: types.Message) -> bool:
    if await _reject_anonymous_command(message):
        return False
    if not message.from_user or message.from_user.id not in Config.OWNERS:
        await _safe_reply(message, "❌ This command is restricted to owners.")
        return False
    return True


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


def _log_command_invocation(message: types.Message, command: str) -> None:
    user_id = message.from_user.id if message.from_user else None
    LOGGER.info(
        "Command invoked: %s by user_id=%s chat_id=%s chat_type=%s",
        command,
        user_id,
        message.chat.id,
        message.chat.type,
    )


def _log_callback_invocation(cb: types.CallbackQuery) -> None:
    from_user_id = cb.from_user.id if cb.from_user else None
    message_chat_id = cb.message.chat.id if cb.message else None
    LOGGER.info(
        "Callback invoked: data=%s from_user_id=%s chat_id=%s",
        cb.data,
        from_user_id,
        message_chat_id,
    )


async def _resolve_user_id(client: Client, raw: str) -> Tuple[Optional[int], Optional[str]]:
    raw = raw.strip()
    if not raw:
        return None, None
    normalized = raw.lstrip("@")
    if raw.isdigit():
        return int(raw), None
    try:
        user = await client.get_users(normalized)
        username = getattr(user, "username", None) or normalized
        return user.id, username
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
        try:
            user = await client.get_users(normalized)
            username = getattr(user, "username", None) or normalized
            return user.id, username
        except RPCError:
            return None, normalized.lower()
    except RPCError:
        return None, normalized.lower()


# -----------------------------
# UI builders
# -----------------------------

def _cb(action: str, *parts: str) -> str:
    if parts:
        return f"{CALLBACK_PREFIX}{action}:" + ":".join(parts)
    return f"{CALLBACK_PREFIX}{action}"


def _sudo_control_panel_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("💌 Send Love", callback_data=_cb("love:send"))],
            [types.InlineKeyboardButton("❓ Help", callback_data=_cb("help"))],
        ]
    )


def _owner_control_panel_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton("➕ Add Sudo", callback_data=_cb("owner:add_sudo")),
                types.InlineKeyboardButton(
                    "📥 Manage Sessions", callback_data=_cb("owner:manage_sessions")
                ),
            ],
            [
                types.InlineKeyboardButton(
                    "📝 Set Log Group", callback_data=_cb("owner:set_log")
                ),
                types.InlineKeyboardButton(
                    "🔐 Set Session Intake Group", callback_data=_cb("owner:set_session")
                ),
            ],
            [
                types.InlineKeyboardButton("💌 Send Love", callback_data=_cb("love:send")),
                types.InlineKeyboardButton("❓ Help", callback_data=_cb("help")),
            ],
        ]
    )


def _user_payment_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton("💳 Payment Plans", callback_data=_cb("payment:info")),
            ],
            [
                types.InlineKeyboardButton(
                    "📤 Send Payment Proof", callback_data=_cb("payment:how")
                )
            ],
        ]
    )


def _help_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        [
            [types.InlineKeyboardButton("⬅️ Back", callback_data=_cb("back"))],
        ]
    )


def _dm_only_message() -> str:
    return "⚠️ This feature is available in private chat. Please DM the bot."


def _callback_private_filter(_: Client, cb: types.CallbackQuery) -> bool:
    return bool(cb.message and cb.message.chat and cb.message.chat.type == enums.ChatType.PRIVATE)


PRIVATE_CALLBACK_FILTER = filters.create(_callback_private_filter)


def _get_control_panel_keyboard(is_owner: bool, has_sudo: bool) -> types.InlineKeyboardMarkup:
    if is_owner:
        return _owner_control_panel_keyboard()
    if has_sudo:
        return _sudo_control_panel_keyboard()
    return _user_payment_keyboard()


def _build_control_panel_text(is_owner: bool, has_sudo: bool) -> str:
    if is_owner:
        return "🧭 **Control Panel**\n\nOwner tools are ready. Choose an action below."
    if has_sudo:
        return "🧭 **Control Panel**\n\nChoose an action below."
    return "💳 **Payment Required**\n\nUse the payment options below to unlock Send Love access."


def _build_remove_session_callback(phone: str) -> str:
    safe_phone = phone.strip()
    prefix = f"{CALLBACK_PREFIX}session:remove:"
    max_length = 64
    if len(prefix) + len(safe_phone) <= max_length:
        return f"{prefix}{safe_phone}"
    token = uuid.uuid4().hex[:12]
    REMOVE_SESSION_TOKENS[token] = safe_phone
    return f"{CALLBACK_PREFIX}session:remove:{token}"


def _resolve_remove_session_target(token: str) -> str:
    return REMOVE_SESSION_TOKENS.pop(token, token)


def _build_help_text(is_owner: bool, has_sudo: bool) -> str:
    if not is_owner and not has_sudo:
        return (
            "💳 **Payment Required**\n\n"
            "Use /start to view payment plans and send payment proof."
        )
    return (
        "🆘 **Help**\n\n"
        "Common commands:\n"
        "• /ping - Check if bot is alive\n"
        "• /preban <user_id or @username> - Queue a pre-ban\n"
        "• /status - Queue status\n"
        "• /cancel - Cancel active Send Love flow\n\n"
        "Send Love flow:\n"
        "1) Tap 💌 Send Love\n"
        "2) Send target username (e.g. @user) or user_id\n"
        "3) Wait for queue updates and results"
    )


async def _build_manage_sessions_view() -> tuple[str, types.InlineKeyboardMarkup]:
    all_s = await get_active_sessions()
    text = f"📑 **Active Sessions ({len(all_s)}):**\n\n"
    kb = []
    for s in all_s:
        text += f"👤 {s['name']} ({s['phone']})\n"
        callback_data = _build_remove_session_callback(s["phone"])
        kb.append([types.InlineKeyboardButton(f"Remove {s['phone']}", callback_data=callback_data)])
    kb.append([types.InlineKeyboardButton("⬅️ Back", callback_data=_cb("back"))])
    return text, types.InlineKeyboardMarkup(kb)


async def _prompt_owner_add_sudo(
    *,
    message: Optional[types.Message] = None,
    cb: Optional[types.CallbackQuery] = None,
) -> None:
    if cb and cb.from_user:
        _set_love_state(cb.from_user.id, "owner_add_sudo")
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "➕ **Add Sudo User**\n\nSend the user ID or @username to grant sudo access.",
            reply_markup=_owner_control_panel_keyboard(),
        )
        return
    if message and message.from_user:
        _set_love_state(message.from_user.id, "owner_add_sudo")
        await _safe_reply(
            message,
            "➕ **Add Sudo User**\n\nSend the user ID or @username to grant sudo access.",
            reply_markup=_owner_control_panel_keyboard(),
        )


async def _grant_sudo_access(
    client: Client,
    message: types.Message,
    raw_target: str,
    *,
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> bool:
    if not raw_target or not raw_target.strip():
        await _safe_reply(message, "❌ Please send a valid user ID or @username.")
        return False
    user_id, username = await _resolve_user_id(client, raw_target)
    if user_id is None:
        await _safe_reply(message, f"❌ Failed to resolve {username or 'user'}.")
        return False
    await give_access(user_id, 24 * 365 * 10)
    await _safe_reply(message, f"✅ Added `{user_id}` as sudo.", reply_markup=reply_markup)
    return True


async def _show_manage_sessions(
    *,
    message: Optional[types.Message] = None,
    cb: Optional[types.CallbackQuery] = None,
) -> None:
    text, markup = await _build_manage_sessions_view()
    if cb:
        await _answer_cb(cb)
        await _safe_edit(cb, text, reply_markup=markup)
        return
    if message:
        await _safe_reply(message, text, reply_markup=markup)


async def _apply_set_log_group(
    chat_id: int,
    *,
    message: Optional[types.Message] = None,
    cb: Optional[types.CallbackQuery] = None,
) -> None:
    await update_setting("log_group", chat_id)
    text = "✅ This group is now the **Log Group**."
    if cb:
        await _answer_cb(cb)
        await _safe_edit(cb, text, reply_markup=_owner_control_panel_keyboard())
        return
    if message:
        await _safe_reply(message, text)


async def _apply_set_session_group(
    chat_id: int,
    chat_type: enums.ChatType,
    *,
    message: Optional[types.Message] = None,
    cb: Optional[types.CallbackQuery] = None,
) -> None:
    if chat_type not in {enums.ChatType.GROUP, enums.ChatType.SUPERGROUP}:
        text = "⚠️ Use /set_session inside the session manager group."
        if cb:
            await _answer_cb(cb)
            await _safe_edit(cb, text, reply_markup=_owner_control_panel_keyboard())
            return
        if message:
            await _safe_reply(message, text)
        return
    await update_setting("session_group", chat_id)
    text = "✅ This group is now the **Session Validation Group**."
    if cb:
        await _answer_cb(cb)
        await _safe_edit(cb, text, reply_markup=_owner_control_panel_keyboard())
        return
    if message:
        await _safe_reply(message, text)


async def _validate_single_session_for_preban(cb: types.CallbackQuery) -> bool:
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
            reply_markup=_get_control_panel_keyboard(is_owner, has_sudo),
        )
        return False
    return True


async def _queue_preban_target(
    client: Client,
    message: types.Message,
    *,
    requester_id: int,
    target_id: Optional[int],
    target_username: Optional[str],
    reply_markup: Optional[types.InlineKeyboardMarkup] = None,
) -> bool:
    if Config.QUEUE_MAXSIZE > 0 and ban_queue.full():
        await _safe_reply(message, "⚠️ Queue is full. Please try again in a moment.")
        return False
    request_id = uuid.uuid4().hex
    event = register_request_event(request_id)
    queued_label = target_id if target_id is not None else f"@{target_username}"
    reply = await _safe_reply_message(
        message,
        "🕒 **Pre-ban queued**\n\n"
        f"Target: `{queued_label}`\n"
        "We'll update this message until processing starts.",
        reply_markup=reply_markup,
    )
    await ban_queue.put(
        (
            {
                "id": target_id,
                "username": target_username,
                "request_id": request_id,
                "notify_chat_id": message.chat.id,
                "notify_message_id": reply.id if reply else None,
            },
            requester_id,
        )
    )
    if reply:
        asyncio.create_task(
            _start_queue_live_updates(
                client,
                message.chat.id,
                reply.id,
                request_id,
                str(queued_label),
                event,
            )
        )
    return True


# -----------------------------
# Handlers
# -----------------------------


def register_ui_and_commands(app: Client) -> None:
    LOGGER.info("Registering UI and command handlers.")

    app.add_handler(
        MessageHandler(_log_commands, command_filter(COMMANDS)),
        group=0,
    )
    app.add_handler(CallbackQueryHandler(_log_callbacks), group=0)
    app.add_handler(
        MessageHandler(_reject_anonymous_group_commands, command_filter(COMMANDS) & MESSAGE_GROUP_FILTER),
        group=1,
    )
    app.add_handler(
        MessageHandler(
            _channel_command_redirect,
            command_filter(
                [
                    "start",
                    "help",
                    "ping",
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
                    "cancel",
                ]
            )
            & filters.channel,
        ),
        group=2,
    )

    app.add_handler(
        MessageHandler(_ping_command, command_filter("ping") & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=3,
    )
    app.add_handler(
        MessageHandler(_help_command, command_filter("help") & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=3,
    )
    app.add_handler(
        MessageHandler(_start_command, command_filter("start") & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=3,
    )
    app.add_handler(
        MessageHandler(_cancel_command, command_filter("cancel") & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(_help_callback, filters.regex(r"^buta:help$") & PRIVATE_CALLBACK_FILTER),
        group=3,
    )
    app.add_handler(
        CallbackQueryHandler(_back_to_panel, filters.regex(r"^buta:back$") & PRIVATE_CALLBACK_FILTER),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(_payment_info, filters.regex(r"^buta:payment:info$") & PRIVATE_CALLBACK_FILTER),
        group=3,
    )
    app.add_handler(
        CallbackQueryHandler(_payment_how, filters.regex(r"^buta:payment:how$") & PRIVATE_CALLBACK_FILTER),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(_love_send, filters.regex(r"^buta:love:send$") & PRIVATE_CALLBACK_FILTER),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(_owner_add_sudo, filters.regex(r"^buta:owner:add_sudo$") & PRIVATE_CALLBACK_FILTER),
        group=3,
    )
    app.add_handler(
        CallbackQueryHandler(
            _owner_manage_sessions, filters.regex(r"^buta:owner:manage_sessions$") & PRIVATE_CALLBACK_FILTER
        ),
        group=3,
    )
    app.add_handler(
        CallbackQueryHandler(_owner_set_log_cb, filters.regex(r"^buta:owner:set_log$") & PRIVATE_CALLBACK_FILTER),
        group=3,
    )
    app.add_handler(
        CallbackQueryHandler(_owner_set_session_cb, filters.regex(r"^buta:owner:set_session$") & PRIVATE_CALLBACK_FILTER),
        group=3,
    )

    app.add_handler(
        MessageHandler(_set_log_group, command_filter("set_log") & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=3,
    )
    app.add_handler(
        MessageHandler(_set_session_group, command_filter("set_session") & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=3,
    )
    app.add_handler(
        MessageHandler(_manage_sessions, command_filter("manage") & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=3,
    )

    app.add_handler(
        CallbackQueryHandler(_remove_session, filters.regex(r"^buta:session:remove:(.+)$")),
        group=3,
    )

    app.add_handler(MessageHandler(_handle_text_messages, filters.text & MESSAGE_PRIVATE_FILTER), group=3)
    app.add_handler(MessageHandler(_preban_user, command_filter("preban") & MESSAGE_PRIVATE_OR_GROUP_FILTER), group=3)
    app.add_handler(MessageHandler(_status_command, command_filter("status") & MESSAGE_PRIVATE_OR_GROUP_FILTER), group=3)
    app.add_handler(MessageHandler(_health_command, command_filter("health") & MESSAGE_PRIVATE_OR_GROUP_FILTER), group=3)
    app.add_handler(MessageHandler(_add_session_command, command_filter("addsession") & MESSAGE_PRIVATE_OR_GROUP_FILTER), group=3)
    app.add_handler(MessageHandler(_add_sudo_command, command_filter("addsudo") & MESSAGE_PRIVATE_OR_GROUP_FILTER), group=3)
    app.add_handler(MessageHandler(_remove_sudo_command, command_filter("remsudo") & MESSAGE_PRIVATE_OR_GROUP_FILTER), group=3)
    app.add_handler(MessageHandler(_set_verify_mode, command_filter("verify") & MESSAGE_PRIVATE_OR_GROUP_FILTER), group=3)
    app.add_handler(
        MessageHandler(_set_verify_delay, command_filter("verify_delay") & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=3,
    )
    app.add_handler(MessageHandler(_set_command, command_filter("set") & MESSAGE_PRIVATE_OR_GROUP_FILTER), group=3)


async def _log_commands(client: Client, message: types.Message) -> None:
    _log_command_update(message)


async def _log_callbacks(client: Client, cb: types.CallbackQuery) -> None:
    _log_callback_invocation(cb)


async def _reject_anonymous_group_commands(client: Client, message: types.Message) -> None:
    if await _reject_anonymous_command(message):
        raise StopPropagation


async def _channel_command_redirect(client: Client, message: types.Message) -> None:
    try:
        await _safe_reply(message, _dm_only_message())
    except Exception:
        LOGGER.exception("Channel redirect handler failed.")


async def _ping_command(client: Client, message: types.Message) -> None:
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "ping")
        session_count = await _get_session_count()
        await _safe_reply(message, f"✅ Bot is active. Sessions loaded: {session_count}")
    except Exception:
        LOGGER.exception("Ping command failed.")
        await _safe_reply(message, "❌ Failed to respond to ping.")


async def _help_command(client: Client, message: types.Message) -> None:
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "help")
        is_owner = message.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(message.from_user.id)
        show_keyboard = message.chat.type == enums.ChatType.PRIVATE
        if not has_sudo:
            payment_list = await _build_payment_list_text()
            text = (
                "💳 **Payment Required**\n\n"
                "Unlock **Send Love** access with a payment plan:\n\n"
                f"{payment_list}\n\n"
                "After payment, tap **Send Payment Proof** to upload your screenshot."
            )
            markup = _user_payment_keyboard() if show_keyboard else None
        else:
            text = _build_help_text(is_owner, has_sudo)
            markup = _help_keyboard() if show_keyboard else None
        await _safe_reply(message, text, reply_markup=markup)
    except Exception:
        LOGGER.exception("Help command failed.")
        await _safe_reply(message, "❌ Failed to load help information.")


async def _start_command(client: Client, message: types.Message) -> None:
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "start")
        is_owner = message.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(message.from_user.id)
        show_keyboard = message.chat.type == enums.ChatType.PRIVATE
        if not show_keyboard:
            await _safe_reply(message, "👋 **Welcome!**\n\nPlease DM me for full instructions.")
            return
        if not has_sudo:
            payment_list = await _build_payment_list_text()
            text = (
                "💳 **Payment Required**\n\n"
                "Unlock **Send Love** access with a payment plan:\n\n"
                f"{payment_list}\n\n"
                "After payment, tap **Send Payment Proof** to upload your screenshot."
            )
            await _safe_reply(message, text, reply_markup=_user_payment_keyboard())
            return
        await _safe_reply(
            message,
            _build_control_panel_text(is_owner, has_sudo),
            reply_markup=_get_control_panel_keyboard(is_owner, has_sudo),
        )
    except Exception:
        LOGGER.exception("Start handler failed.")
        await _safe_reply(message, "❌ Something went wrong. Please try again.")


async def _cancel_command(client: Client, message: types.Message) -> None:
    try:
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, "cancel")
        if not message.from_user:
            return
        _clear_love_state(message.from_user.id)
        await _safe_reply(message, "✅ Cancelled. You can use **Send Love** again anytime.")
    except Exception:
        LOGGER.exception("Cancel command failed.")
        await _safe_reply(message, "❌ Failed to cancel. Please try again.")


async def _help_callback(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user:
            return
        is_owner = cb.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(cb.from_user.id)
        if not has_sudo:
            await _answer_cb(cb, "Payment required.", show_alert=True)
            payment_list = await _build_payment_list_text()
            text = (
                "💳 **Payment Required**\n\n"
                "Unlock **Send Love** access with a payment plan:\n\n"
                f"{payment_list}\n\n"
                "After payment, tap **Send Payment Proof** to upload your screenshot."
            )
            await _safe_edit(cb, text, reply_markup=_user_payment_keyboard())
            return
        await _answer_cb(cb)
        text = _build_help_text(is_owner, has_sudo)
        await _safe_edit(cb, text, reply_markup=_help_keyboard())
    except Exception:
        LOGGER.exception("Help callback failed.")
        await _safe_edit(cb, "❌ Failed to load help information.")


async def _back_to_panel(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user:
            return
        await _answer_cb(cb)
        is_owner = cb.from_user.id in Config.OWNERS
        has_sudo = is_owner or await has_access(cb.from_user.id)
        if not has_sudo:
            payment_list = await _build_payment_list_text()
            text = (
                "💳 **Payment Required**\n\n"
                "Unlock **Send Love** access with a payment plan:\n\n"
                f"{payment_list}\n\n"
                "After payment, tap **Send Payment Proof** to upload your screenshot."
            )
            await _safe_edit(cb, text, reply_markup=_user_payment_keyboard())
            return
        await _safe_edit(
            cb,
            _build_control_panel_text(is_owner, has_sudo),
            reply_markup=_get_control_panel_keyboard(is_owner, has_sudo),
        )
    except Exception:
        LOGGER.exception("Back to panel handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _payment_info(client: Client, cb: types.CallbackQuery) -> None:
    try:
        await _answer_cb(cb)
        payment_list = await _build_payment_list_text()
        await _safe_edit(
            cb,
            "💳 **Payment to Send Love**\n\n"
            f"{payment_list}\n\n"
            "Please complete payment and send your screenshot in this chat.\n"
            "Tap **Send Payment Proof** after payment for approval.",
            reply_markup=_user_payment_keyboard(),
        )
    except Exception:
        LOGGER.exception("Payment info handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _payment_how(client: Client, cb: types.CallbackQuery) -> None:
    try:
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "📤 **Send Payment Proof**\n\n"
            "Upload your payment proof image here. We'll verify and activate your access.",
            reply_markup=_user_payment_keyboard(),
        )
    except Exception:
        LOGGER.exception("Payment how handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _love_send(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user:
            return
        is_owner = cb.from_user.id in Config.OWNERS
        if not is_owner and not await has_access(cb.from_user.id):
            await _answer_cb(cb, "Payment required to send love.", show_alert=True)
            payment_list = await _build_payment_list_text()
            await _safe_edit(
                cb,
                "💳 **Payment Required**\n\n"
                f"{payment_list}\n\n"
                "Please complete payment to activate **Send Love** access.",
                reply_markup=_user_payment_keyboard(),
            )
            return
        if not await _validate_single_session_for_preban(cb):
            return
        await _answer_cb(cb)
        _set_love_state(cb.from_user.id, "awaiting_target")
        await _safe_edit(
            cb,
            "💌 **Send Love**\n\n"
            "Send target username (e.g. @user) or user_id.\n"
            "Use /cancel to stop this flow.",
            reply_markup=_get_control_panel_keyboard(is_owner, True),
        )
    except Exception:
        LOGGER.exception("Love send handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_panel(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        await _safe_edit(
            cb,
            "🧭 **Control Panel**\n\n"
            "Owner tools are ready. Choose an action below.",
            reply_markup=_owner_control_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner panel handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_add_session(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        _set_love_state(cb.from_user.id, "owner_add_session")
        await _safe_edit(
            cb,
            "➕ **Add Session**\n\n"
            "Send the new session string to add a session.",
            reply_markup=_owner_control_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner add session handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_add_session_prompt(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        _set_love_state(cb.from_user.id, "owner_add_session")
        await _safe_edit(
            cb,
            "🆔 **Send the session string** to add a new session.",
            reply_markup=_owner_control_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner add session prompt failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_add_sudo(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _prompt_owner_add_sudo(cb=cb)
    except Exception:
        LOGGER.exception("Owner add sudo handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_remove_sudo(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        _set_love_state(cb.from_user.id, "owner_remove_sudo")
        await _safe_edit(
            cb,
            "➖ **Remove Sudo User**\n\n"
            "Send the user ID or @username to revoke sudo access.",
            reply_markup=_owner_control_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner remove sudo handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_add_prompt(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        _set_love_state(cb.from_user.id, "owner_add_sudo")
        await _safe_edit(
            cb,
            "🆔 **Send User ID or @username** to grant sudo access.",
            reply_markup=_owner_control_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner add prompt handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_remove_prompt(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        _set_love_state(cb.from_user.id, "owner_remove_sudo")
        await _safe_edit(
            cb,
            "🆔 **Send User ID or @username** to revoke sudo access.",
            reply_markup=_owner_control_panel_keyboard(),
        )
    except Exception:
        LOGGER.exception("Owner remove prompt handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_sudo_list(client: Client, cb: types.CallbackQuery) -> None:
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
        await _safe_edit(cb, text, reply_markup=_owner_control_panel_keyboard())
    except Exception:
        LOGGER.exception("Owner sudo list handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_manage_sessions(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _show_manage_sessions(cb=cb)
    except Exception:
        LOGGER.exception("Owner manage sessions handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_set_log_cb(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _apply_set_log_group(cb.message.chat.id, cb=cb)
    except Exception:
        LOGGER.exception("Owner set log handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _owner_set_session_cb(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _apply_set_session_group(
            cb.message.chat.id,
            cb.message.chat.type,
            cb=cb,
        )
    except Exception:
        LOGGER.exception("Owner set session handler failed.")
        await _safe_edit(cb, "❌ Something went wrong. Please try again.")


async def _help_verify_toggle(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        mode = "on"
        if cb.data.endswith("off"):
            mode = "off"
        await update_setting("verify_enabled", mode == "on")
        await _safe_edit(cb, f"✅ Verification mode set to {mode}.", reply_markup=_help_keyboard())
    except Exception:
        LOGGER.exception("Help verify toggle failed.")
        await _safe_edit(cb, "❌ Failed to update verification mode.")


async def _help_manage_sessions(client: Client, cb: types.CallbackQuery) -> None:
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
            callback_data = _build_remove_session_callback(s["phone"])
            kb.append([types.InlineKeyboardButton(f"Remove {s['phone']}", callback_data=callback_data)])
        kb.append([types.InlineKeyboardButton("⬅️ Back", callback_data=_cb("back"))])
        await _safe_edit(cb, text, reply_markup=types.InlineKeyboardMarkup(kb))
    except Exception:
        LOGGER.exception("Help manage sessions failed.")
        await _safe_edit(cb, "❌ Failed to load sessions.")


async def _set_log_group(client: Client, message: types.Message) -> None:
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "set_log")
        await _apply_set_log_group(message.chat.id, message=message)
    except Exception:
        LOGGER.exception("Set log command failed.")
        await _safe_reply(message, "❌ Failed to set log group.")


async def _set_session_group(client: Client, message: types.Message) -> None:
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "set_session")
        await _apply_set_session_group(message.chat.id, message.chat.type, message=message)
    except Exception:
        LOGGER.exception("Set session command failed.")
        await _safe_reply(message, "❌ Failed to set session group.")


async def _manage_sessions(client: Client, message: types.Message) -> None:
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "manage")
        await _show_manage_sessions(message=message)
    except Exception:
        LOGGER.exception("Manage sessions command failed.")
        await _safe_reply(message, "❌ Failed to load sessions.")


async def _remove_session(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        token = cb.matches[0].group(1)
        phone = _resolve_remove_session_target(token)
        await deactivate_session(phone)
        await _safe_edit(cb, f"✅ Removed session for {phone}.")
    except Exception:
        LOGGER.exception("Remove session callback failed.")
        await _safe_edit(cb, "❌ Failed to remove session.")


async def _handle_text_messages(client: Client, message: types.Message) -> None:
    try:
        if not message.from_user or not message.text:
            return
        state = _get_love_state(message.from_user.id)
        if not state:
            return
        if state == "owner_add_session":
            if message.from_user.id not in Config.OWNERS:
                return
            session_string = message.text.strip()
            if not session_string:
                await _safe_reply(message, "❌ Please send a valid session string.")
                return
            success = await _add_session_from_string(message, session_string)
            if success:
                _clear_love_state(message.from_user.id)
            return
        if state in {"owner_add_sudo", "owner_remove_sudo"}:
            if message.from_user.id not in Config.OWNERS:
                return
            if state == "owner_add_sudo":
                if not await _grant_sudo_access(
                    client,
                    message,
                    message.text,
                    reply_markup=_owner_control_panel_keyboard(),
                ):
                    return
            else:
                user_id, username = await _resolve_user_id(client, message.text)
                if user_id is None and username is None:
                    await _safe_reply(message, "❌ Please send a valid user ID or @username.")
                    return
                if user_id is None and username is not None:
                    await _safe_reply(message, "❌ Unable to resolve that username.")
                    return
                await revoke_access(user_id)
                await _safe_reply(
                    message,
                    f"✅ Removed `{user_id}` from sudo.",
                    reply_markup=_owner_control_panel_keyboard(),
                )
            _clear_love_state(message.from_user.id)
            return
        if state == "awaiting_target":
            is_owner = message.from_user.id in Config.OWNERS
            if not is_owner and not await has_access(message.from_user.id):
                await _safe_reply(message, "❌ You are not authorized. Send payment proof to get access.")
                _clear_love_state(message.from_user.id)
                return
            has_sudo = True
            target_id, target_username = await _resolve_user_id(client, message.text)
            if target_id is None and target_username is None:
                await _safe_reply(message, "❌ Failed to resolve user.")
                return
            queued = await _queue_preban_target(
                client,
                message,
                requester_id=message.from_user.id,
                target_id=target_id,
                target_username=target_username,
                reply_markup=_get_control_panel_keyboard(is_owner, has_sudo),
            )
            if queued:
                _clear_love_state(message.from_user.id)
    except Exception:
        LOGGER.exception("Handle text handler failed.")
        await _safe_reply(message, "❌ Something went wrong. Please try again.")


async def _add_session_from_string(message: types.Message, session_string: str) -> bool:
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
                reply_markup=_owner_control_panel_keyboard(),
            )
            return False
        await _safe_reply(
            message,
            f"✅ Session added for {me.first_name}.",
            reply_markup=_owner_control_panel_keyboard(),
        )
        return True
    except Exception:
        LOGGER.exception("Add session from string failed.")
        await _safe_reply(
            message,
            "❌ Failed to add session.",
            reply_markup=_owner_control_panel_keyboard(),
        )
        return False
    finally:
        if started:
            try:
                await temp.stop()
            except Exception:
                LOGGER.exception("Failed to stop temporary session.")


async def _preban_user(client: Client, message: types.Message) -> None:
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

        target_id, target_username = await _resolve_user_id(client, message.command[1])

        if target_id is None and target_username is None:
            await _safe_reply(message, "❌ Failed to resolve user.")
            return

        await _queue_preban_target(
            client,
            message,
            requester_id=message.from_user.id,
            target_id=target_id,
            target_username=target_username,
        )
    except Exception:
        LOGGER.exception("Preban command failed.")
        await _safe_reply(message, "❌ Failed to queue pre-ban request.")


async def _status_command(client: Client, message: types.Message) -> None:
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


async def _health_command(client: Client, message: types.Message) -> None:
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


async def _add_session_command(client: Client, message: types.Message) -> None:
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "addsession")
        raw_text = message.text or ""
        parts = raw_text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await _safe_reply(message, "Usage: /addsession <session_string>")
            return
        session_string = parts[1].strip()
        await _add_session_from_string(message, session_string)
    except Exception:
        LOGGER.exception("Add session command failed.")
        await _safe_reply(message, "❌ Failed to add session.")


async def _add_sudo_command(client: Client, message: types.Message) -> None:
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "addsudo")
        if len(message.command) < 2:
            await _prompt_owner_add_sudo(message=message)
            return
        await _grant_sudo_access(client, message, message.command[1])
    except Exception:
        LOGGER.exception("Add sudo command failed.")
        await _safe_reply(message, "❌ Failed to add sudo user.")


async def _remove_sudo_command(client: Client, message: types.Message) -> None:
    try:
        if not await _require_owner(message):
            return
        _log_command_invocation(message, "remsudo")
        if len(message.command) < 2:
            await _safe_reply(message, "Usage: /remsudo <user_id or @username>")
            return
        user_id, username = await _resolve_user_id(client, message.command[1])
        if user_id is None:
            await _safe_reply(message, f"❌ Failed to resolve {username or 'user'}.")
            return
        await revoke_access(user_id)
        await _safe_reply(message, f"✅ Removed `{user_id}` from sudo.")
    except Exception:
        LOGGER.exception("Remove sudo command failed.")
        await _safe_reply(message, "❌ Failed to remove sudo user.")


async def _set_verify_mode(client: Client, message: types.Message) -> None:
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


async def _set_verify_delay(client: Client, message: types.Message) -> None:
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


async def _set_command(client: Client, message: types.Message) -> None:
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
                "/set approval_durations <comma-separated hours>\n"
                "/set payment_rates <hours:price, hours:price>",
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
        if key == "payment_rates":
            raw_pairs = [p.strip() for p in value.split(",") if p.strip()]
            rates: dict[int, str] = {}
            for pair in raw_pairs:
                if ":" in pair:
                    hours_str, price = pair.split(":", 1)
                elif "=" in pair:
                    hours_str, price = pair.split("=", 1)
                else:
                    await _safe_reply(message, "❌ payment_rates format: hours:price, hours:price")
                    return
                try:
                    hours = int(hours_str.strip())
                except ValueError:
                    await _safe_reply(message, "❌ payment_rates hours must be integers.")
                    return
                if hours <= 0 or not price.strip():
                    await _safe_reply(message, "❌ payment_rates entries must include hours and price.")
                    return
                rates[hours] = price.strip()
            await update_setting("payment_rates", rates)
            await _safe_reply(message, "✅ Payment rates updated.")
            return
        await _safe_reply(message, "❌ Unknown setting key. Use /set for usage.")
    except Exception:
        LOGGER.exception("Set command failed.")
        await _safe_reply(message, "❌ Failed to update settings.")


# -----------------------------
# Fallbacks
# -----------------------------


def register_fallbacks(app: Client) -> None:
    LOGGER.info("Registering fallback handlers.")
    app.add_handler(
        MessageHandler(_unknown_command, filters.text & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=100,
    )
    app.add_handler(
        MessageHandler(_fallback_command_response, filters.text & MESSAGE_PRIVATE_OR_GROUP_FILTER),
        group=200,
    )


async def _unknown_command(client: Client, message: types.Message) -> None:
    try:
        if not message.text:
            return
        command = _extract_command(message.text)
        if not command or command in COMMANDS:
            return
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, f"unknown:{command}")
        await _safe_reply(
            message,
            "❓ Unknown command.\nUse /help to see available commands.",
        )
    except Exception:
        LOGGER.exception("Unknown command handler failed.")
        await _safe_reply(message, "❌ Failed to process command.")


async def _fallback_command_response(client: Client, message: types.Message) -> None:
    try:
        if not message.text or getattr(message, "_buta_replied", False):
            return
        command = _extract_command(message.text)
        if not command or command not in COMMANDS:
            return
        if await _reject_anonymous_command(message):
            return
        _log_command_invocation(message, f"fallback:{command}")
        await _safe_reply(
            message,
            "✅ Command received, but I couldn't process it right now.\n"
            "Please try again or use /help for available commands.",
        )
    except Exception:
        LOGGER.exception("Fallback command handler failed.")
        await _safe_reply(message, "❌ Failed to process command.")
