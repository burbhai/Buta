from __future__ import annotations

import logging

from pyrogram import filters, types
from pyrogram.errors import RPCError

from bot_instance import bot
from config import Config
from db import get_settings, give_access

LOGGER = logging.getLogger(__name__)


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


@bot.on_message(filters.photo & filters.group)
async def payment_group_redirect(bot, message):
    try:
        await _safe_reply(message, "⚠️ Please DM the bot to submit payment proof.")
    except Exception:
        LOGGER.exception("Payment group redirect failed.")


@bot.on_message(filters.photo & filters.private)
async def handle_payment_screenshot(bot, message):
    try:
        if not message.from_user:
            return
        conf = await get_settings()
        log_group = conf.get("log_group")

        if not log_group:
            await _safe_reply(message, "Admin hasn't setup log group yet.")
            return

        # Forward to log group with approval buttons
        kb = types.InlineKeyboardMarkup(
            [
                [types.InlineKeyboardButton("Approve 24h", callback_data=f"app_{message.from_user.id}_24")],
                [types.InlineKeyboardButton("Reject", callback_data=f"rej_{message.from_user.id}")],
            ]
        )

        await message.forward(log_group)
        await bot.send_message(log_group, f"💳 **New Payment** from `{message.from_user.id}`", reply_markup=kb)
        await _safe_reply(message, "🕒 Screenshot sent. Wait for admin approval.")
    except Exception:
        LOGGER.exception("Payment screenshot handler failed.")
        await _safe_reply(message, "❌ Failed to submit payment proof.")

@bot.on_callback_query(filters.regex(r"app_(\d+)_(\d+)") & filters.user(Config.OWNERS))
async def approve_user(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        _, uid, hours = cb.data.split("_")
        await give_access(int(uid), int(hours))
        await bot.send_message(int(uid), "✅ **Payment Approved!** You can now send usernames.")
        await cb.edit_message_text(f"✅ Approved User {uid}")
    except RPCError:
        await _answer_cb(cb, "❌ Failed to approve user.", show_alert=True)
    except Exception:
        LOGGER.exception("Approve payment handler failed.")
        await _answer_cb(cb, "❌ Failed to approve user.", show_alert=True)

@bot.on_callback_query(filters.regex(r"rej_(\d+)") & filters.user(Config.OWNERS))
async def reject_user(bot, cb):
    try:
        if not cb.from_user or cb.from_user.id not in Config.OWNERS:
            await _answer_cb(cb, "Owner only.", show_alert=True)
            return
        await _answer_cb(cb)
        uid = cb.matches[0].group(1)
        await bot.send_message(int(uid), "❌ **Payment Rejected.** Please contact the admin.")
        await cb.edit_message_text(f"❌ Rejected User {uid}")
    except RPCError:
        await _answer_cb(cb, "❌ Failed to reject user.", show_alert=True)
    except Exception:
        LOGGER.exception("Reject payment handler failed.")
        await _answer_cb(cb, "❌ Failed to reject user.", show_alert=True)
