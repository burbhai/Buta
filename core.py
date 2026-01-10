import time
import threading
from typing import Dict, List, Tuple

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import QUEUE_CHECK_DELAY, TASK_COOLDOWN

# ─────────────────────────────────────────────
# RUNTIME STORAGE (IN-MEMORY)
# ─────────────────────────────────────────────

USER_ACCESS: Dict[int, float] = {}  # user_id -> expiry timestamp
WAITING_USERS = set()                # users waiting to send username
QUEUE: List[Tuple[int, str]] = []    # (user_id, username)
ACTIVE_TASK = False

LOG_GROUP_ID = None
SESSION_GROUP_ID = None

# Session: {"id": int, "active": bool, "client": Client}
SESSIONS = []

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

    return (
        "💼 **My Access**\n\n"
        f"Time remaining: **{hrs}h {mins}m**\n\n"
        "You can continue sending usernames 🌿"
    )


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
    Stub: manual approval flow
    Owner manually approves -> grant_access(user_id, hours)
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
        status = "✅ ON" if sess["active"] else "❌ OFF"
        rows.append([InlineKeyboardButton(f"Session {idx + 1} {status}", callback_data="noop")])

    return "🧠 **Sessions Overview**", InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────
# SYSTEM STATUS
# ─────────────────────────────────────────────

def get_system_status() -> str:
    with LOCK:
        q_len = len(QUEUE)
    return (
        "📊 **System Status**\n\n"
        f"Active task: {'Yes' if ACTIVE_TASK else 'No'}\n"
        f"Queue length: {q_len}\n"
        f"Log group set: {'Yes' if LOG_GROUP_ID else 'No'}\n"
        f"Session group set: {'Yes' if SESSION_GROUP_ID else 'No'}"
    )


# ─────────────────────────────────────────────
# BACKGROUND WORKER + MULTI-SESSION PRE-BAN
# ─────────────────────────────────────────────

def start_worker(app: Client):
    global ACTIVE_TASK

    def worker_loop():
        global ACTIVE_TASK

        while True:
            try:
                with LOCK:
                    if ACTIVE_TASK or not QUEUE:
                        time.sleep(QUEUE_CHECK_DELAY)
                        continue
                    user_id, username = QUEUE.pop(0)
                    ACTIVE_TASK = True

                # ── PROCESSING START ──
                start_time = time.time()
                ban_success = 0
                ban_failed = 0

                # Iterate all active sessions
                for sess in SESSIONS:
                    if not sess.get("active") or not sess.get("client"):
                        continue

                    client: Client = sess["client"]

                    try:
                        async def preban():
                            async for dialog in client.get_dialogs():
                                chat = dialog.chat
                                # Only supergroups or channels
                                if chat.type not in ["supergroup", "channel"]:
                                    continue
                                # Check admin rights
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

                        app.loop.run_until_complete(preban())

                    except Exception:
                        continue  # skip faulty session

                elapsed = round(time.time() - start_time, 2)

                # Log group
                if LOG_GROUP_ID:
                    try:
                        app.send_message(
                            LOG_GROUP_ID,
                            f"✅ Pre-ban completed for {username}\n"
                            f"Success: {ban_success} | Failed: {ban_failed} | Time: {elapsed}s"
                        )
                    except Exception:
                        pass

                # Notify requesting user
                try:
                    app.send_message(
                        user_id,
                        f"💫 Your target @{username} has been processed.\n"
                        f"Success: {ban_success} | Failed: {ban_failed}"
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
