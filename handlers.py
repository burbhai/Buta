from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from config import OWNER_IDS, ACCESS_PLANS
import core
import db

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
# REGISTER ALL HANDLERS
# ─────────────────────────────────────────────
def register(app):

    # ─── /start ───────────────────────────────
    @app.on_message(filters.command("start") & filters.private)
    async def start_cmd(client, message: Message):
        uid = message.from_user.id
        if is_owner(uid):
            await message.reply("👑 **Owner Control Panel**\nChoose an option below:", reply_markup=owner_keyboard())
            return
        await message.reply(
            "Hey 👋\nWelcome to **StartLove ✨**\nWe quietly help keep things calm & stress-free 💙",
            reply_markup=user_keyboard()
        )

    # ─── CALLBACK QUERIES ─────────────────────
    @app.on_callback_query()
    async def callbacks(client, cb: CallbackQuery):
        uid = cb.from_user.id
        data = cb.data

        # ── OWNER CALLBACKS
        if data.startswith("owner:"):
            if not is_owner(uid):
                await cb.answer("Not allowed", show_alert=True)
                return
            action = data.split(":", 1)[1]

            if action == "set_log":
                await cb.message.reply(
                    "🔧 **Set Log Group**\n"
                    "1️⃣ Add me to your target group\n"
                    "2️⃣ Make me admin\n"
                    "3️⃣ Send `/set_log` in that group"
                )
            elif action == "set_session":
                await cb.message.reply(
                    "🗂 **Set Session Group**\n"
                    "1️⃣ Create private group\n"
                    "2️⃣ Add me as admin\n"
                    "3️⃣ Send `/set_session` in that group"
                )
            elif action == "manage_sessions":
                text, keyboard = core.get_sessions_overview()
                await cb.message.reply(text, reply_markup=keyboard)
            elif action == "status":
                status_text = core.get_system_status()
                await cb.message.reply(status_text)
            await cb.answer()
            return

        # ── USER CALLBACKS
        if data.startswith("user:"):
            action = data.split(":", 1)[1]

            if action == "start":
                if not core.has_active_access(uid):
                    await cb.message.reply("💔 You don’t have active access. Tap **Get Access** to continue.")
                    await cb.answer()
                    return
                core.mark_waiting_for_username(uid)
                await cb.message.reply(
                    "✨ Please send the **username** you want us to quietly handle.\nExample: `@username`"
                )

            elif action == "get_access":
                await cb.message.reply("🔓 **Unlock Access**\nChoose your duration:", reply_markup=access_plans_keyboard())

            elif action == "my_access":
                info = core.get_access_info(uid)
                await cb.message.reply(info)

            elif action == "help":
                await cb.message.reply(
                    "ℹ️ **Help**\n"
                    "• Unlock access to start\n"
                    "• Send usernames during active access\n"
                    "• Requests are handled one by one\n\nThat’s it 🌿"
                )

            elif action.startswith("plan:"):
                plan_key = action.split(":", 1)[1]
                hours = ACCESS_PLANS.get(plan_key)
                if not hours:
                    await cb.answer("Invalid plan", show_alert=True)
                    return
                db.create_payment_request(uid, hours)
                await cb.message.reply(
                    f"💳 **Access Verification**\nSelected: **{hours}h**\n"
                    "Complete payment and send screenshot. Admin will verify."
                )
            await cb.answer()
            return

    # ─── /set_log (owner only)
    @app.on_message(filters.command("set_log") & filters.group)
    async def set_log_group(client, message: Message):
        if not is_owner(message.from_user.id): return
        core.set_log_group(message.chat.id)
        await message.reply("✅ Log group set successfully.")

    # ─── /set_session (owner only)
    @app.on_message(filters.command("set_session") & filters.group)
    async def set_session_group(client, message: Message):
        if not is_owner(message.from_user.id): return
        core.set_session_group(message.chat.id)
        await message.reply("✅ Session group connected. You can send session strings here (owner only).")

    # ─── USER SENDS USERNAME
    @app.on_message(filters.private & filters.text)
    async def username_receiver(client, message: Message):
        uid = message.from_user.id
        if not core.is_waiting_for_username(uid):
            return

        username = message.text.strip()
        if not username.startswith("@") or len(username) < 4:
            await message.reply("❌ Please send a valid username starting with @")
            return

        if not core.has_active_access(uid):
            await message.reply("💔 Your access has expired.")
            core.clear_waiting(uid)
            return

        position = core.enqueue_request(uid, username)
        if position == 0:
            await message.reply("💫 We’re taking care of this now. Please relax 🌿")
        else:
            await message.reply(f"⏳ You’re in line. Queue position: **#{position}**")
