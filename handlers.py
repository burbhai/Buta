from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional, Tuple, Dict, Any

from pyrogram import Client, StopPropagation, filters, types
from pyrogram.handlers import MessageHandler
from pyrogram.errors import FloodWait, RPCError

# Internal Project Imports
from bot_instance import bot
from config import Config
from core import ban_queue, get_worker_status
from db import (
    add_session,
    deactivate_session,
    get_active_sessions,
    get_active_sudo_users,
    check_db_health,
    give_access,
    has_access,
    revoke_access,
    update_setting,
)
from queue_handler import get_queue_snapshot, get_queue_status

# --- Logging & Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
LOGGER = logging.getLogger(__name__)

# TTL state tracking for interactive flows
STATE_TRACKER: Dict[int, Dict[str, Any]] = {}
STATE_TTL = 600 # 10 minutes
REMOVE_TOKENS: Dict[str, str] = {}

COMMAND_PREFIXES = Config.COMMAND_PREFIXES
GROUP_FILTER = filters.group | (filters.supergroup if hasattr(filters, "supergroup") else filters.group)

# --- Helper Utilities ---

def _update_state(user_id: int, state_name: str):
    """Sets a temporary conversational state for a user."""
    STATE_TRACKER[user_id] = {"state": state_name, "time": time.time()}

def _get_clear_state(user_id: int) -> Optional[str]:
    """Retrieves and clears state if expired, else returns state name."""
    entry = STATE_TRACKER.get(user_id)
    if not entry:
        return None
    if time.time() - entry["time"] > STATE_TTL:
        STATE_TRACKER.pop(user_id, None)
        return None
    return entry["state"]

async def _resolve_target(client: Client, input_str: str) -> Tuple[Optional[int], Optional[str]]:
    """Resolves raw input into a valid Telegram User ID or Username."""
    clean = input_str.strip().lstrip("@")
    if not clean: return None, None
    if clean.isdigit(): return int(clean), None
    try:
        user = await client.get_users(clean)
        return user.id, None
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        return await _resolve_target(client, clean)
    except RPCError:
        return None, clean

async def _safe_send(chat_id: int, text: str, markup=None):
    """Utility to ensure messages are sent even if specific reply fails."""
    try:
        return await bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        LOGGER.error(f"Failed to send message to {chat_id}: {e}")

# --- Keyboards ---

def kb_main(is_owner: bool, has_sudo: bool):
    buttons = [
        [
            types.InlineKeyboardButton("❤️ Send Love", callback_data="flow_love"),
            types.InlineKeyboardButton("🆘 Help", callback_data="flow_help"),
        ],
        [types.InlineKeyboardButton("🔁 System Status", callback_data="flow_ping")]
    ]
    if is_owner:
        buttons.append([types.InlineKeyboardButton("👑 Owner Control Panel", callback_data="admin_panel")])
    elif not has_sudo:
        buttons.append([types.InlineKeyboardButton("💳 Get Sudo Access", callback_data="payment_info")])
    return types.InlineKeyboardMarkup(buttons)

def kb_admin():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("➕ Add Sudo", callback_data="admin_add_sudo"),
            types.InlineKeyboardButton("➖ Rem Sudo", callback_data="admin_rem_sudo")
        ],
        [
            types.InlineKeyboardButton("📑 Sessions", callback_data="admin_manage_sessions"),
            types.InlineKeyboardButton("📄 Sudo List", callback_data="admin_list_sudo")
        ],
        [types.InlineKeyboardButton("🔙 Back to Home", callback_data="flow_home")]
    ])

# --- Commands ---

@bot.on_message(filters.command("start", prefixes=COMMAND_PREFIXES))
async def cmd_start(client, message):
    if not message.from_user: return
    is_owner = message.from_user.id in Config.OWNERS
    has_sudo = is_owner or await has_access(message.from_user.id)
    
    msg = (
        "✨ **Welcome to the Pre-Ban Manager**\n\n"
        "Use the menu below to manage targets or check system health."
    )
    await _safe_send(message.chat.id, msg, markup=kb_main(is_owner, has_sudo))

@bot.on_message(filters.command("preban", prefixes=COMMAND_PREFIXES))
async def cmd_preban(client, message):
    if not message.from_user: return
    if not (message.from_user.id in Config.OWNERS or await has_access(message.from_user.id)):
        return await message.reply("❌ **Access Denied.** You need Sudo privileges.")

    if len(message.command) < 2:
        return await message.reply("📝 **Usage:** `/preban @username` or `/preban user_id`")

    uid, uname = await _resolve_target(client, message.command[1])
    if not uid and not uname:
        return await message.reply("❌ **Error:** Could not resolve user.")

    await ban_queue.put(({"id": uid, "username": uname}, message.from_user.id))
    await message.reply(f"✅ **Queued:** `{uid or uname}` has been added to the processing list.")

# --- Conversational State Handler ---

@bot.on_message(filters.private & filters.text & ~filters.command(True))
async def handle_conversations(client, message):
    uid = message.from_user.id
    state = _get_clear_state(uid)
    if not state: return

    # Flow: Send Love (Targeting)
    if state == "wait_love_target":
        t_uid, t_uname = await _resolve_target(client, message.text)
        if not t_uid and not t_uname:
            return await message.reply("❌ Invalid user. Please send a valid ID or @username.")
        
        await ban_queue.put(({"id": t_uid, "username": t_uname}, uid))
        STATE_TRACKER.pop(uid, None)
        await message.reply(f"🚀 **Success:** `{t_uid or t_uname}` is now in the queue.")

    # Flow: Add Sudo (Owner Only)
    elif state == "wait_sudo_id" and uid in Config.OWNERS:
        t_uid, _ = await _resolve_target(client, message.text)
        if t_uid:
            await give_access(t_uid, 87600) # Long term access
            await message.reply(f"✅ User `{t_uid}` successfully granted Sudo access.")
        else:
            await message.reply("❌ Could not resolve ID. Operation cancelled.")
        STATE_TRACKER.pop(uid, None)

# --- Callback Queries ---

@bot.on_callback_query()
async def handle_callbacks(client, cb):
    uid = cb.from_user.id
    is_owner = uid in Config.OWNERS
    has_sudo = is_owner or await has_access(uid)

    if cb.data == "flow_home":
        await cb.message.edit_text("🏠 **Main Menu**", reply_markup=kb_main(is_owner, has_sudo))

    elif cb.data == "flow_love":
        if not has_sudo:
            return await cb.answer("❌ Sudo Access Required.", show_alert=True)
        _update_state(uid, "wait_love_target")
        await cb.message.edit_text("💌 **New Pre-Ban Target**\n\nSend the **@username** or **User ID** you wish to queue.", 
                                   reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Cancel", callback_data="flow_home")]]))

    elif cb.data == "admin_panel":
        if not is_owner: return await cb.answer("Unauthorized.", show_alert=True)
        await cb.message.edit_text("⚙️ **Owner Control Panel**", reply_markup=kb_admin())

    elif cb.data == "admin_add_sudo":
        _update_state(uid, "wait_sudo_id")
        await cb.message.edit_text("👤 **Add Sudo User**\n\nSend the User ID or @username to grant access.")

    elif cb.data == "flow_ping":
        db = "✅" if await check_db_health() else "❌"
        sess = await get_active_sessions()
        await cb.answer(f"Database: {db} | Sessions: {len(sess)}", show_alert=True)

# --- Management Commands ---

@bot.on_message(filters.command("addsession", prefixes=COMMAND_PREFIXES))
async def cmd_add_session(client, message):
    if message.from_user.id not in Config.OWNERS: return
    if len(message.command) < 2:
        return await message.reply("Usage: `/addsession <session_string>`")
    
    s_string = message.text.split(None, 1)[1]
    temp = Client("temp_val", session_string=s_string, api_id=Config.API_ID, api_hash=Config.API_HASH)
    
    try:
        await temp.start()
        me = await temp.get_me()
        await add_session(s_string, me.first_name, me.phone_number or str(me.id))
        await message.reply(f"✅ **Session Verified:** Added account `{me.first_name}`.")
        await temp.stop()
    except Exception as e:
        await message.reply(f"❌ **Validation Failed:** `{e}`")

@bot.on_message(filters.command("status", prefixes=COMMAND_PREFIXES))
async def cmd_status(client, message):
    if not (message.from_user.id in Config.OWNERS or await has_access(message.from_user.id)):
        return
    status_text = await get_queue_status()
    await message.reply(status_text)
