import time
import threading
import asyncio
from typing import Dict, List, Tuple
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import RPCError
from config import QUEUE_CHECK_DELAY, TASK_COOLDOWN

# ─────────────────────────────────────────────
# RUNTIME STORAGE
# ─────────────────────────────────────────────
USER_ACCESS: Dict[int, float] = {}       # user_id -> expiry timestamp
WAITING_USERS = set()                     # users waiting to send username
QUEUE: List[Tuple[int, str]] = []        # (user_id, username)
ACTIVE_TASK = False
LOG_GROUP_ID = None
SESSION_GROUP_ID = None

# Session dict: {"id": int, "active": bool, "string": str, "client": Client, "owner_id": int}
SESSIONS: List[Dict] = []

LOCK = threading.Lock()

# ─────────────────────────────────────────────
# ACCESS MANAGEMENT
# ─────────────────────────────────────────────
def has_active_access(user_id: int) -> bool:
    expiry = USER_ACCESS.get(user_id)
    return bool(expiry and time.time() < expiry)

def grant_access(user_id: int, hours: int):
    USER_ACCESS[user_id] = time.time() + (hours * 3600)

def get_access_info(user_id: int) -> str:
    if not has_active_access(user_id):
        return "💔 You don’t have any active access."
    remaining = int(USER_ACCESS[user_id] - time.time())
    hrs = remaining // 3600
    mins = (remaining % 3600) // 60
    return f"💼 **My Access**\n\nTime remaining: **{hrs}h {mins}m**\nYou can continue sending usernames 🌿"

# ─────────────────────────────────────────────
# WAITING USERS MANAGEMENT
# ─────────────────────────────────────────────
def mark_waiting_for_username(user_id: int):
    WAITING_USERS.add(user_id)

def is_waiting_for_username(user_id: int) -> bool:
    return user_id in WAITING_USERS

def clear_waiting(user_id: int):
    WAITING_USERS.discard(user_id)

# ─────────────────────────────────────────────
# QUEUE MANAGEMENT
# ─────────────────────────────────────────────
def enqueue_request(user_id: int, username: str) -> int:
    with LOCK:
        clear_waiting(user_id)
        QUEUE.append((user_id, username))
        return len(QUEUE) - 1  # 0 = processing now

# ─────────────────────────────────────────────
# OWNER GROUP SETTERS
# ─────────────────────────────────────────────
def set_log_group(chat_id: int):
    global LOG_GROUP_ID
    LOG_GROUP_ID = chat_id

def set_session_group(chat_id: int):
    global SESSION_GROUP_ID
    SESSION_GROUP_ID = chat_id

# ─────────────────────────────────────────────
# PAYMENT STUB
# ─────────────────────────────────────────────
def create_payment_request(user_id: int, hours: int):
    """
    Manual approval flow: owner approves -> call grant_access(user_id, hours)
    """
    pass

# ─────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────
def get_sessions_overview():
    if not SESSIONS:
        return "🧠 **Sessions**\n\nNo sessions added yet.", None
    rows = []
    for idx, sess in enumerate(SESSIONS):
        status = "✅ ON" if sess.get("active") else "❌ OFF"
        rows.append([InlineKeyboardButton(f"Session {idx+1} {status}", callback_data="noop")])
    return "🧠 **Sessions Overview**", InlineKeyboardMarkup(rows)

# ─────────────────────────────────────────────
# SYSTEM STATUS
# ─────────────────────────────────────────────
def get_system_status() -> str:
    with LOCK:
        q_len = len(QUEUE)
    return f"📊 **System Status**\nActive task: {'Yes' if ACTIVE_TASK else 'No'}\nQueue length: {q_len}\nLog group set: {'Yes' if LOG_GROUP_ID else 'No'}\nSession group set: {'Yes' if SESSION_GROUP_ID else 'No'}"

# ─────────────────────────────────────────────
# BACKGROUND WORKER + MULTI-SESSION PRE-BAN
# ─────────────────────────────────────────────
def start_worker(app: Client):
    """
    Processes QUEUE, multi-session pre-ban using validated sessions.
    """
    global ACTIVE_TASK

    def worker_loop():
        global ACTIVE_TASK
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while True:
            try:
                with LOCK:
                    if ACTIVE_TASK or not QUEUE:
                        time.sleep(QUEUE_CHECK_DELAY)
                        continue
                    user_id, username = QUEUE.pop(0)
                    ACTIVE_TASK = True

                start_time = time.time()
                ban_success = 0
                ban_failed = 0

                async def process_user():
                    nonlocal ban_success, ban_failed

                    for sess in SESSIONS:
                        if not sess.get("active"):
                            continue

                        # Lazy initialize client if not yet created
                        if not sess.get("client"):
                            try:
                                sess_client = Client(
                                    name=f"session_{sess['id']}",
                                    session_string=sess["string"],
                                    api_id=int(app.api_id),
                                    api_hash=app.api_hash
                                )
                                await sess_client.start()
                                sess["client"] = sess_client
                            except Exception:
                                sess["active"] = False
                                continue

                        client: Client = sess["client"]

                        try:
                            async for dialog in client.get_dialogs():
                                chat = dialog.chat
                                if chat.type not in ["supergroup", "channel"]:
                                    continue

                                # Check if client is admin
                                try:
                                    me = await client.get_me()
                                    member = await client.get_chat_member(chat.id, me.id)
                                    if member.status not in ["administrator", "creator"]:
                                        continue
                                except Exception:
                                    continue

                                # Ban target user
                                try:
                                    target = await client.get_users(username)
                                    await client.ban_chat_member(chat.id, target.id)
                                    ban_success += 1
                                except Exception:
                                    ban_failed += 1
                        except Exception:
                            sess["active"] = False
                            continue

                loop.run_until_complete(process_user())
                elapsed = round(time.time() - start_time, 2)

                # Log group notification
                if LOG_GROUP_ID:
                    try:
                        app.send_message(
                            LOG_GROUP_ID,
                            f"✅ Pre-ban completed for {username}\nSuccess: {ban_success} | Failed: {ban_failed} | Time: {elapsed}s"
                        )
                    except Exception:
                        pass

                # Notify user
                try:
                    app.send_message(
                        user_id,
                        f"💫 @{username} processed.\nSuccess: {ban_success} | Failed: {ban_failed}"
                    )
                except Exception:
                    pass

                with LOCK:
                    ACTIVE_TASK = False
                time.sleep(TASK_COOLDOWN)

            except Exception:
                with LOCK:
                    ACTIVE_TASK = False
                time.sleep(QUEUE_CHECK_DELAY)

    thread = threading.Thread(target=worker_loop, daemon=True)
    thread.start()
