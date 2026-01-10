from pyrogram import filters
from pyrogram.types import Message
import db
import core
from config import OWNER_IDS

def register_payment_handler(app):
    @app.on_message(filters.private & filters.photo)
    async def payment_screenshot(client, message: Message):
        uid = message.from_user.id
        log_group_id = db.db.get_collection("settings").find_one({"name":"log_group"}) or None
        caption = f"💰 Payment from @{message.from_user.username or uid}\nUser ID: {uid}"
        if log_group_id:
            try: await client.send_photo(chat_id=log_group_id["value"], photo=message.photo.file_id, caption=caption)
            except: pass
        await message.reply("💳 Payment received! Admin will verify shortly.")

    @app.on_message(filters.command("approve") & filters.group)
    async def approve_user(client, message: Message):
        admin_id = message.from_user.id
        if admin_id not in OWNER_IDS: return
        args = message.text.split()
        if len(args)<2: await message.reply("Usage: /approve <user_id>"); return
        try: target_id = int(args[1])
        except: await message.reply("❌ Invalid ID"); return
        success = db.approve_payment(target_id)
        if not success:
            await message.reply(f"❌ No pending payment for {target_id}")
            return
        await message.reply(f"✅ User {target_id} approved. Access granted!")
        try: await client.send_message(target_id,"🎉 Your payment is verified. Access granted!")
        except: pass
