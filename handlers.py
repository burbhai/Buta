from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from db import has_access
from core import queue
from payment_handler import payment_keyboard, save_payment, approve_payment
from config import OWNER_IDS


def register_handlers(app):

    @app.on_message(filters.command("start"))
    async def start(_, m):
        if m.from_user.id in OWNER_IDS:
            await m.reply("👑 Owner Panel")
        else:
            await m.reply(
                "Welcome to Pre-Ban Bot",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Get Access", callback_data="get_access")],
                    [InlineKeyboardButton("My Access", callback_data="my_access")]
                ])
            )

    @app.on_callback_query(filters.regex("get_access"))
    async def access(_, q):
        await q.message.edit("Choose plan:", reply_markup=payment_keyboard())

    @app.on_callback_query(filters.regex("pay_"))
    async def pay(_, q):
        plan = q.data.replace("pay_", "")
        save_payment(q.from_user.id, plan)
        await q.message.edit("📸 Send payment screenshot")

    @app.on_message(filters.text & filters.private)
    async def submit(_, m):
        if not has_access(m.from_user.id):
            return await m.reply("❌ No active access")

        await queue.put((m.text.strip(), m.from_user.id))
        await m.reply("⏳ Added to queue")
