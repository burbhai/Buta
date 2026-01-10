from pymongo import MongoClient
from datetime import datetime, timedelta
from config import MONGO_URI, DB_NAME

# ─────────────────────────────────────────────
# MONGO CLIENT / DATABASE
# ─────────────────────────────────────────────
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# Collections
users_col = db["users"]            # stores users and access info
payments_col = db["payments"]      # stores pending payment requests
settings_col = db["settings"]      # stores log_group, session_group, etc.
sessions_col = db["sessions"]      # optional: active sessions

# ─────────────────────────────────────────────
# USER ACCESS MANAGEMENT
# ─────────────────────────────────────────────
def grant_access(user_id: int, hours: int):
    expiry = datetime.utcnow() + timedelta(hours=hours)
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"expiry": expiry}},
        upsert=True
    )

def get_user(user_id: int):
    return users_col.find_one({"user_id": user_id})

def has_active_access(user_id: int) -> bool:
    user = get_user(user_id)
    if not user or "expiry" not in user:
        return False
    return datetime.utcnow() < user["expiry"]

# ─────────────────────────────────────────────
# PAYMENT MANAGEMENT
# ─────────────────────────────────────────────
def create_payment(user_id: int, hours: int):
    """Add a pending payment request"""
    payments_col.update_one(
        {"user_id": user_id},
        {"$set": {"hours": hours, "created_at": datetime.utcnow(), "status": "pending"}},
        upsert=True
    )

def get_pending_payment(user_id: int):
    return payments_col.find_one({"user_id": user_id, "status": "pending"})

def approve_payment(user_id: int) -> bool:
    """Mark payment as approved"""
    payment = get_pending_payment(user_id)
    if not payment:
        return False
    payments_col.update_one({"user_id": user_id}, {"$set": {"status": "approved"}})
    grant_access(user_id, payment.get("hours", 6))
    return True

# ─────────────────────────────────────────────
# SETTINGS (LOG GROUP, SESSION GROUP)
# ─────────────────────────────────────────────
def set_setting(key: str, value):
    settings_col.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

def get_setting(key: str):
    setting = settings_col.find_one({"key": key})
    return setting["value"] if setting else None

def set_log_group(chat_id: int):
    set_setting("log_group", chat_id)

def set_session_group(chat_id: int):
    set_setting("session_group", chat_id)

# ─────────────────────────────────────────────
# SESSION MANAGEMENT (OPTIONAL)
# ─────────────────────────────────────────────
def add_session(session_id: int, session_string: str, active=True):
    sessions_col.update_one(
        {"session_id": session_id},
        {"$set": {"session_string": session_string, "active": active}},
        upsert=True
    )

def get_sessions():
    return list(sessions_col.find())

def toggle_session(session_id: int, active: bool):
    sessions_col.update_one({"session_id": session_id}, {"$set": {"active": active}})
