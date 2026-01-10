import time
import threading
from typing import Dict, List, Tuple

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import QUEUE_CHECK_DELAY, TASK_COOLDOWN


# ─────────────────────────────────────────────
# RUNTIME STORAGE (IN-MEMORY, SAFE)
# ─────────────────────────────────────────────

# Access: user_id -> expiry_timestamp
USER_ACCESS: Dict[int, float] = {}

# Users waiting to send username
WAITING_USERS = set()

# Queue: list of (user_id, username)
QUEUE: List[Tuple[int, str]] = []

# Currently running task flag
ACTIVE_TASK = False

# Owner-defined groups
LOG_GROUP_ID = None
SESSION_GROUP_ID = None

# Fake session store (for UI only)
SESSIONS = []  # list of {"id": int, "active": bool}

# Thread lock
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
# WAITING STATE (USERNAME INPUT)
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
    """
    Add request to queue.
    Returns queue position (0 = processing now).
    """
    with LOCK:
        clear_waiting(user_id)
        QUEUE.append((user_id, username))
        position = len(QUEUE) - 1
        return position


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
# PAYMENT (STUB – MANUAL APPROVAL READY)
# ─────────────────────────────────────────────

def create_payment_request(user_id: int, hours: int):
    """
    Stub: create a payment request.
    In real usage, owner manually approves and calls grant_access().
    """
    # This is intentionally simple & safe
    # Owner approval logic can call grant_access(user_id, hours)
    pass


# ─────────────────────────────────────────────
# SESSION UI (OWNER SIDE)
# ─────────────────────────────────────────────

def get_sessions_overview():
    if not SESSIONS:
        return (
            "🧠 **Sessions**\n\nNo sessions added yet.",
            None
        )

    rows = []
    for idx, sess in enumerate(SESSIONS):
        status = "✅ ON" if sess["active"] else "❌ OFF"
        rows.append([
            InlineKeyboardButton(
                f"Session {idx + 1} {status}",
                callback_data=f"noop"
            )
        ])

    return (
        "🧠 **Sessions Overview**",
        InlineKeyboardMarkup(rows)
    )


# ─────────────────────────────────────────────
# SYSTEM STATUS
# ─────────────────────────────────────────────

def get_system_status() -> str:
    with LOCK:
        q_len = len(QUEUE)

    return (
        "📊 **System Status**\n\n"
        f"Active task: **{'Yes' if ACTIVE_TASK else 'No'}**\n"
        f"Queue length: **{q_len}**\n"
        f"Log group set: **{'Yes' if LOG_GROUP_ID else 'No'}**\n"
        f"Session group set: **{'Yes' if SESSION_GROUP_ID else 'No'}**"
    )


# ─────────────────────────────────────────────
# BACKGROUND WORKER (QUEUE PROCESSOR)
# ─────────────────────────────────────────────

def start_worker(app):
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

                # ── SIMULATED PROCESSING ──
                # This represents internal handling.
                # Replace ONLY this block later if needed.
                time.sleep(TASK_COOLDOWN)

                # Optional log
                if LOG_GROUP_ID:
                    try:
                        app.send_message(
                            LOG_GROUP_ID,
                            f"✅ Task completed for {username}"
                        )
                    except Exception:
                        pass

                with LOCK:
                    ACTIVE_TASK = False

            except Exception:
                # Never let worker crash
                with LOCK:
                    ACTIVE_TASK = False
                time.sleep(QUEUE_CHECK_DELAY)

    thread = threading.Thread(target=worker_loop, daemon=True)
    thread.start()
