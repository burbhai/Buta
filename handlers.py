from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.enums import ChatType
import core
import db
from config import OWNER_IDS, ACCESS_PLANS

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS

def owner_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Set Log Group", callback_data="owner:set_log")],
        [InlineKeyboardButton("🗂 Set Session Group", callback_data="owner:set_session")],
        [InlineKeyboardButton("🧠 Manage Sessions", callback_data="owner:manage_sessions")],
        [InlineKeyboardButton("📊 System Status", callback_data="owner:status")]
    ])

def user_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💖 Start", callback_data="user:start")],
        [InlineKeyboardButton("🔓 Get Access", callback_data="user:get_access")],
        [InlineKeyboardButton("💼 My Access", callback_data="user:my_access")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="user:help")]
    ])

def access_plans_keyboard():
    rows = []
    for key, hours in ACCESS_PLANS.items():
        rows.append([InlineKeyboardButton(f"⏳ {hours} Hours", callback_data=f"user:plan:{key}")])
    return InlineKeyboardMarkup(rows)

# ─────────────────────────────────────────────
# REGISTER HANDLERS
# ─────────────────────────────────────────────
def register(app):

    @app.on_message(filters.command("start") & filters.private)
    async def start_cmd(client, message: Message):
        uid = message.from_user.id

        if is_owner(uid):
            await message.reply(
                "👑 **Owner Control Panel**\n\nChoose an option below:",
                reply_markup=owner_keyboard()
            )
            return

        await message.reply(
            "Hey 👋 Welcome to **StartLove ✨**\nSend usernames quietly 🌿",
            reply_markup=user_keyboard()
        )

    @app.on_callback_query()
    async def callbacks(client, cb):
        uid = cb.from_user.id
        data = cb.data

        if data.startswith("owner:"):
            if not is_owner(uid):
                await cb.answer("Not allowed", show_alert=True)
                return
            action = data.split(":", 1)[1]
            if action == "set_log":
                await cb.message.reply(
                    "🔧 Add me to the group & make admin, then send /set_log here."
                )
            elif action == "set_session":
                await cb.message.reply(
                    "🗂 Add me to a private group & make admin, then send /set_session here."
                )
            elif action == "manage_sessions":
                text, keyboard = core.get_sessions_overview()
                await cb.message.reply(text, reply_markup=keyboard)
            elif action == "status":
                await cb.message.reply(core.get_system_status())
            await cb.answer()
            return

        if data.startswith("user:"):
            action = data.split(":", 1)[1]
            if action == "start":
                if not core.has_active_access(uid):
                    await cb.message.reply("💔 No active access. Tap Get Access.")
                    await cb.answer()
                    return
                core.mark_waiting_for_username(uid)
                await cb.message.reply("✨ Send the **username** you want us to handle.\nExample: `@username`")
            elif action == "get_access":
                await cb.message.reply("🔓 Choose access duration:", reply_markup=access_plans_keyboard())
            elif action == "my_access":
                await cb.message.reply(core.get_access_info(uid))
            elif action == "help":
                await cb.message.reply("ℹ️ Send usernames during active access. Requests handled one by one.")
            elif action.startswith("plan:"):
                plan_key = action.split(":", 1)[1]
                hours = ACCESS_PLANS.get(plan_key)
                if not hours:
                    await cb.answer("Invalid plan", show_alert=True)
                    return
                db.create_payment_request(uid, hours)
                await cb.message.reply(f"💳 Selected: {hours}h. Upload screenshot for verification.")
            await cb.answer()
            return

    @app.on_message(filters.command("set_log") & filters.group)
    async def set_log_group(client, message: Message):
        if not is_owner(message.from_user.id):
            return
        core.set_log_group(message.chat.id)
        await message.reply("✅ Log group set successfully.")

    @app.on_message(filters.command("set_session") & filters.group)
    async def set_session_group(client, message: Message):
        if not is_owner(message.from_user.id):
            return
        core.set_session_group(message.chat.id)
        await message.reply("✅ Session group connected. Owner can send sessions here.")

    @app.on_message(filters.private & filters.text)
    async def username_receiver(client, message: Message):
        uid = message.from_user.id
        if not core.is_waiting_for_username(uid):
            return
        username = message.text.strip()
        if not username.startswith("@") or len(username) < 4:
            await message.reply("❌ Invalid username.")
            return
        if not core.has_active_access(uid):
            await message.reply("💔 Your access expired.")
            core.clear_waiting(uid)
            return
        position = core.enqueue_request(uid, username)
        if position == 0:
            await message.reply("💫 Processing now. Relax 🌿")
        else:
            await message.reply(f"⏳ In queue. Position: **#{position}**")
