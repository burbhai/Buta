from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from db import payments, grant_access
from config import DEFAULT_PLANS


def payment_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("6 Hours", callback_data="pay_6h")],
        [InlineKeyboardButton("12 Hours", callback_data="pay_12h")],
        [InlineKeyboardButton("24 Hours", callback_data="pay_24h")]
    ])


def save_payment(user_id, plan):
    payments.insert_one({
        "user_id": user_id,
        "plan": plan,
        "approved": False
    })


def approve_payment(user_id, plan):
    grant_access(user_id, DEFAULT_PLANS[plan])
    payments.update_one(
        {"user_id": user_id},
        {"$set": {"approved": True}}
    )
