from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import OWNER_IDS, ACCESS_PLANS
import core
import db

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def is_owner(uid): return uid in OWNER_IDS

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
    rows=[]
    for k,hours in ACCESS_PLANS.items():
        rows.append([InlineKeyboardButton(f"⏳ {hours} Hours", callback_data=f"user:plan:{k}")])
    return InlineKeyboardMarkup(rows)

# ─────────────────────────────────────────────
# REGISTER HANDLERS
# ─────────────────────────────────────────────
def register(app):

    @app.on_message(filters.command("start") & filters.private)
    async def start_cmd(client,message):
        uid=message.from_user.id
        if is_owner(uid):
            await message.reply("👑 Owner Panel:", reply_markup=owner_keyboard())
            return
        await message.reply(
            "Hey 👋 Welcome to StartLove ✨\nWe keep things calm 💙",
            reply_markup=user_keyboard()
        )

    @app.on_callback_query()
    async def callbacks(client, cb):
        uid=cb.from_user.id
        data=cb.data

        # OWNER
        if data.startswith("owner:"):
            if not is_owner(uid):
                await cb.answer("Not allowed", show_alert=True)
                return
            action=data.split(":",1)[1]
            if action=="set_log":
                await cb.message.reply("🔧 Add bot to group & run `/set_log`")
            elif action=="set_session":
                await cb.message.reply("🗂 Add bot to private session group & run `/set_session`")
            elif action=="manage_sessions":
                text,keyboard=core.get_sessions_overview()
                await cb.message.reply(text,reply_markup=keyboard)
            elif action=="status":
                status=core.get_system_status()
                await cb.message.reply(status)
            await cb.answer(); return

        # USER
        if data.startswith("user:"):
            action=data.split(":",1)[1]
            if action=="start":
                if not core.has_active_access(uid):
                    await cb.message.reply("💔 No active access. Tap Get Access.")
                    await cb.answer(); return
                core.mark_waiting_for_username(uid)
                await cb.message.reply("✨ Send @username to process.")
            elif action=="get_access":
                await cb.message.reply("🔓 Unlock Access", reply_markup=access_plans_keyboard())
            elif action=="my_access":
                info=core.get_access_info(uid)
                await cb.message.reply(info)
            elif action=="help":
                await cb.message.reply("ℹ️ Help: Unlock access -> send usernames -> requests handled sequentially.")
            elif action.startswith("plan:"):
                key=action.split(":",1)[1]
                hours=ACCESS_PLANS.get(key)
                if not hours:
                    await cb.answer("Invalid plan", show_alert=True); return
                db.create_payment_request(uid,hours)
                await cb.message.reply(f"💳 Selected {hours}h. Send payment screenshot. Admin will verify.")
            await cb.answer(); return

    # SET LOG GROUP
    @app.on_message(filters.command("set_log") & filters.group)
    async def set_log(client,message):
        if not is_owner(message.from_user.id): return
        core.set_log_group(message.chat.id)
        await message.reply("✅ Log group set.")

    # SET SESSION GROUP
    @app.on_message(filters.command("set_session") & filters.group)
    async def set_session(client,message):
        if not is_owner(message.from_user.id): return
        core.set_session_group(message.chat.id)
        await message.reply("✅ Session group set.")

    # USER SENDS USERNAME
    @app.on_message(filters.private & filters.text)
    async def username_receiver(client,message):
        uid=message.from_user.id
        if not core.is_waiting_for_username(uid): return
        username=message.text.strip()
        if not username.startswith("@") or len(username)<4:
            await message.reply("❌ Invalid username")
            return
        if not core.has_active_access(uid):
            await message.reply("💔 Access expired"); core.clear_waiting(uid); return
        pos=core.enqueue_request(uid,username)
        if pos==0:
            await message.reply("💫 Processing now 🌿")
        else:
            await message.reply(f"⏳ In queue #{pos}")
