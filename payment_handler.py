from __future__ import annotations

import logging
from datetime import datetime, timedelta

from pyrogram import Client, filters, types
from pyrogram.errors import RPCError
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from config import Config
from db import (
    get_payment_request,
    get_settings,
    give_access,
    mark_payment_status,
    record_payment_request,
)

LOGGER = logging.getLogger(__name__)

CALLBACK_PREFIX = "buta:payment:"


async def _safe_reply(message: types.Message, text: str) -> None:
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
    try:
        if text is None:
            await cb.answer()
        else:
            await cb.answer(text, show_alert=show_alert)
    except Exception:
        LOGGER.exception("Failed to answer callback query.")


async def _safe_send(client: Client, chat_id: int, text: str) -> None:
    try:
        await client.send_message(chat_id, text)
    except Exception:
        LOGGER.exception("Failed to send message to chat_id=%s.", chat_id)


def _build_approval_keyboard(user_id: int, durations: list[int]) -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                f"Approve {hours}h", callback_data=f"{CALLBACK_PREFIX}approve:{user_id}:{hours}"
            )
        ]
        for hours in durations
    ]
    rows.append(
        [
            types.InlineKeyboardButton(
                "Reject", callback_data=f"{CALLBACK_PREFIX}reject:{user_id}"
            )
        ]
    )
    return types.InlineKeyboardMarkup(rows)


def _sanitize_durations(raw: object) -> list[int]:
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


async def _payment_group_redirect(client: Client, message: types.Message) -> None:
    try:
        await _safe_reply(message, "⚠️ Please DM the bot to submit payment proof.")
    except Exception:
        LOGGER.exception("Payment group redirect failed.")


async def _handle_payment_screenshot(client: Client, message: types.Message) -> None:
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
        admin_msg = await client.send_message(
            log_group,
            f"💳 **New Payment** from `{message.from_user.id}`",
            reply_markup=kb,
        )
        await record_payment_request(
            user_id=message.from_user.id,
            chat_id=log_group,
            message_id=admin_msg.id,
        )
        await _safe_reply(message, "🕒 Screenshot sent. Wait for admin approval.")
    except Exception:
        LOGGER.exception("Payment screenshot handler failed.")
        await _safe_reply(message, "❌ Failed to submit payment proof.")


async def _approve_user(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        data = cb.data or ""
        if data.startswith("app_"):
            _, uid_str, hours_str = data.split("_", 2)
        else:
            _, _, payload = data.partition("approve:")
            uid_str, hours_str = payload.split(":", 1)
        existing = await get_payment_request(cb.message.id)
        if existing and existing.get("status") in {"approved", "rejected"}:
            await _answer_cb(cb, "Already processed.", show_alert=True)
            return
        await give_access(int(uid_str), int(hours_str))
        conf = await get_settings()
        approval_text = conf.get(
            "approval_text",
            "✅ **Payment Approved!** You can now send usernames.",
        )
        await _safe_send(client, int(uid_str), approval_text)
        await mark_payment_status(cb.message.id, "approved", cb.from_user.id)
        expiry = datetime.utcnow() + timedelta(hours=int(hours_str))
        await cb.edit_message_text(
            f"✅ Approved User {uid_str} for {hours_str}h\nExpires: {expiry.isoformat()}Z"
        )
        await _safe_send(
            client,
            cb.message.chat.id,
            f"✅ Approved `{uid_str}` for {hours_str}h by `{cb.from_user.id}`.",
        )
    except RPCError:
        await _answer_cb(cb, "❌ Failed to approve user.", show_alert=True)
    except Exception:
        LOGGER.exception("Approve payment handler failed.")
        await _answer_cb(cb, "❌ Failed to approve user.", show_alert=True)


async def _reject_user(client: Client, cb: types.CallbackQuery) -> None:
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        data = cb.data or ""
        if data.startswith("rej_"):
            _, uid = data.split("_", 1)
        else:
            uid = data.split(":", 1)[1]
        existing = await get_payment_request(cb.message.id)
        if existing and existing.get("status") in {"approved", "rejected"}:
            await _answer_cb(cb, "Already processed.", show_alert=True)
            return
        await _safe_send(
            client,
            int(uid),
            "❌ **Payment Rejected.** Please contact the admin.",
        )
        await mark_payment_status(cb.message.id, "rejected", cb.from_user.id)
        await cb.edit_message_text(f"❌ Rejected User {uid}")
        await _safe_send(
            client,
            cb.message.chat.id,
            f"❌ Rejected `{uid}` by `{cb.from_user.id}`.",
        )
    except RPCError:
        await _answer_cb(cb, "❌ Failed to reject user.", show_alert=True)
    except Exception:
        LOGGER.exception("Reject payment handler failed.")
        await _answer_cb(cb, "❌ Failed to reject user.", show_alert=True)


def register_payment(app: Client) -> None:
    LOGGER.info("Registering payment handlers.")
    app.add_handler(MessageHandler(_payment_group_redirect, filters.photo & filters.group), group=5)
    app.add_handler(MessageHandler(_handle_payment_screenshot, filters.photo & filters.private), group=5)
    app.add_handler(
        CallbackQueryHandler(
            _approve_user,
            filters.regex(r"^(?:buta:payment:approve:\d+:\d+|app_\d+_\d+)$")
            & filters.user(Config.OWNERS),
        ),
        group=5,
    )
    app.add_handler(
        CallbackQueryHandler(
            _reject_user,
            filters.regex(r"^(?:buta:payment:reject:\d+|rej_\d+)$") & filters.user(Config.OWNERS),
        ),
        group=5,
    )
