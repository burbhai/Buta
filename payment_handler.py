from pyrogram import filters
from pyrogram.types import Message
import db

def register_payment_handler(app):
    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client,message:Message):
        uid=message.from_user.id
        pending=db.payments.find_one({"user_id":uid,"status":"pending"})
        if not pending:
            await message.reply("❌ No pending payment.")
            return
        log_group=db.get_setting("log_group")
        if log_group:
            caption=f"💰 Payment from @{message.from_user.username or uid}\nUserID:{uid}\nPlan:{pending['hours']}h"
            try: await client.send_photo(int(log_group),photo=message.photo.file_id,caption=caption)
            except: pass
        await message.reply("💳 Payment received. Admin will verify.")
