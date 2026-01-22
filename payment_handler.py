from __future__ import annotations

import logging

from pyrogram import filters, types
from pyrogram.errors import RPCError

from bot_instance import bot
from config import Config
from db import get_settings, give_access

LOGGER = logging.getLogger(__name__)


async def _safe_reply(message: types.Message, text: str) -> None:
    """Safely reply to a message."""
    try:
        await message.reply(text)
    except Exception:
        LOGGER.exception("Failed to reply to message.")


async def _answer_cb(
    cb: types.CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> None:
    """Answer a callback query."""
    try:
        if text is None:
            await cb.answer()
        else:
            await cb.answer(text, show_alert=show_alert)
    except Exception:
        LOGGER.exception("Failed to answer callback query.")


async def _safe_send(bot, chat_id: int, text: str) -> None:
    """Safely send a message to a chat."""
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        LOGGER.exception("Failed to send message to chat_id=%s.", chat_id)


def _build_approval_keyboard(user_id: int, durations: list[int]) -> types.InlineKeyboardMarkup:
    """Build approval keyboard with dynamic durations."""
    rows = [
        [types.InlineKeyboardButton(f"Approve {hours}h", callback_data=f"app_{user_id}_{hours}")]
        for hours in durations
    ]
    rows.append([types.InlineKeyboardButton("Reject", callback_data=f"rej_{user_id}")])
    return types.InlineKeyboardMarkup(rows)


def _sanitize_durations(raw: object) -> list[int]:
    """Normalize duration list with sane defaults."""
    defaults = [24, 72]
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


@bot.on_message(filters.photo & filters.group)
async def payment_group_redirect(bot, message):
    """Redirect payment screenshots to DM."""
    try:
        await _safe_reply(message, "⚠️ Please DM the bot to submit payment proof.")
    except Exception:
        LOGGER.exception("Payment group redirect failed.")


@bot.on_message(filters.photo & filters.private)
async def handle_payment_screenshot(bot, message):
    """Handle payment proof screenshots in DM."""
    try:
        if not message.from_user:
            return
        conf = await get_settings()
        log_group = conf.get("log_group")

        if not log_group:
            await _safe_reply(
                message,
                "⚠️ Payment review is not configured yet. Please contact an admin.",
            )
            return

        durations = _sanitize_durations(conf.get("approval_durations"))
        try:
            default_duration = int(conf.get("default_duration", durations[0]))
        except (TypeError, ValueError):
            default_duration = durations[0]
        if default_duration not in durations:
            durations = [default_duration, *durations]
        kb = _build_approval_keyboard(message.from_user.id, durations)

        await message.forward(log_group)
        await bot.send_message(
            log_group,
            f"💳 **New Payment** from `{message.from_user.id}`",
            reply_markup=kb,
        )
        await _safe_reply(message, "🕒 Screenshot sent. Wait for admin approval.")
    except Exception:
        LOGGER.exception("Payment screenshot handler failed.")
        await _safe_reply(message, "❌ Failed to submit payment proof.")

@bot.on_callback_query(filters.regex(r"app_(\d+)_(\d+)") & filters.user(Config.OWNERS))
async def approve_user(bot, cb):
    """Approve a payment request."""
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        _, uid, hours = cb.data.split("_")
        await give_access(int(uid), int(hours))
        conf = await get_settings()
        approval_text = conf.get(
            "approval_text",
            "✅ **Payment Approved!** You can now send usernames.",
        )
        await _safe_send(bot, int(uid), approval_text)
        await cb.edit_message_text(f"✅ Approved User {uid} for {hours}h")
        await _safe_send(
            bot,
            cb.message.chat.id,
            f"✅ Approved `{uid}` for {hours}h by `{cb.from_user.id}`.",
        )
    except RPCError:
        await _answer_cb(cb, "❌ Failed to approve user.", show_alert=True)
    except Exception:
        LOGGER.exception("Approve payment handler failed.")
        await _answer_cb(cb, "❌ Failed to approve user.", show_alert=True)

@bot.on_callback_query(filters.regex(r"rej_(\d+)") & filters.user(Config.OWNERS))
async def reject_user(bot, cb):
    """Reject a payment request."""
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        uid = cb.matches[0].group(1)
        await _safe_send(
            bot,
            int(uid),
            "❌ **Payment Rejected.** Please contact the admin.",
        )
        await cb.edit_message_text(f"❌ Rejected User {uid}")
        await _safe_send(
            bot,
            cb.message.chat.id,
            f"❌ Rejected `{uid}` by `{cb.from_user.id}`.",
        )
    except RPCError:
        await _answer_cb(cb, "❌ Failed to reject user.", show_alert=True)
    except Exception:
        LOGGER.exception("Reject payment handler failed.")
        await _answer_cb(cb, "❌ Failed to reject user.", show_alert=True)
