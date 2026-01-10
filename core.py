import time
import threading
from typing import Dict, List, Tuple
import asyncio
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import QUEUE_CHECK_DELAY, TASK_COOLDOWN
import db

# ─────────────────────────────────────────────
# RUNTIME STORAGE (IN-MEMORY)
# ─────────────────────────────────────────────
WAITING_USERS = set()                # users waiting to send username
QUEUE: List[Tuple[int, str]] = []    # (user_id, username)
ACTIVE_TASK = False

# Session dict: {"id": int, "active": bool, "client": Client}
SESSIONS: List[Dict] = []

LOCK = threading.Lock()

# ─────────────────────────────────────────────
# ACCESS MANAGEMENT
# ─────────────────────────────────────────────
def has_active_access(user_id: int) -> bool:
    return db.has_active_access(user_id)

def grant_access(user_id: int, hours: int):
    db.grant_access(user_id, hours)

def get_access_info(user_id: int) -> str:
    return db.get_access_info(user_id)

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
# OWNER GROUP / SESSION MANAGEMENT
# ─────────────────────────────────────────────
def set_log_group(chat_id: int):
    db.set_setting("log_group", chat_id)

def set_session_group(chat_id: int):
    db.set_setting("session_group", chat_id)

def get_sessions_overview():
    if not SESSIONS:
        return "🧠 **Sessions**\n\nNo sessions added yet.", None

    rows = []
    for idx, sess in enumerate(SESSIONS):
        status = "✅ ON" if sess.get("active") else "❌ OFF"
        rows.append([InlineKeyboardButton(f"Session {idx + 1} {status}", callback_data="noop")])

    return "🧠 **Sessions Overview**", InlineKeyboardMarkup(rows)

def get_system_status() -> str:
    with LOCK:
        q_len = len(QUEUE)
    log_group = db.get_setting("log_group")
    session_group = db.get_setting("session_group")
    return (
        "📊 **System Status**\n\n"
        f"Active task: {'Yes' if ACTIVE_TASK else 'No'}\n"
        f"Queue length: {q_len}\n"
        f"Log group set: {'Yes' if log_group else 'No'}\n"
        f"Session group set: {'Yes' if session_group else 'No'}"
    )

# ─────────────────────────────────────────────
# BACKGROUND WORKER + MULTI-SESSION PRE-BAN
# ─────────────────────────────────────────────
def start_worker(app: Client):
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
                        if not sess.get("active") or not sess.get("client"):
                            continue
                        client: Client = sess["client"]
                        try:
                            async for dialog in client.get_dialogs():
                                chat = dialog.chat
                                if chat.type not in ["supergroup", "channel"]:
                                    continue

                                try:
                                    me = await client.get_me()
                                    member = await client.get_chat_member(chat.id, me.id)
                                    if member.status not in ["administrator", "creator"]:
                                        continue
                                except Exception:
                                    continue

                                try:
                                    target = await client.get_users(username)
                                    await client.ban_chat_member(chat.id, target.id)
                                    ban_success += 1
                                except Exception:
                                    ban_failed += 1
                        except Exception:
                            continue

                loop.run_until_complete(process_user())
                elapsed = round(time.time() - start_time, 2)

                # Log group
                log_group = db.get_setting("log_group")
                if log_group:
                    try:
                        app.send_message(
                            log_group,
                            f"✅ Pre-ban completed for {username}\n"
                            f"Success: {ban_success} | Failed: {ban_failed} | Time: {elapsed}s"
                        )
                    except Exception:
                        pass

                # Notify user
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
