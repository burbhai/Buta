from pymongo import MongoClient
from datetime import datetime, timedelta

from config import MONGO_URI, DB_NAME


# ─────────────────────────────────────────────
# DATABASE INIT
# ─────────────────────────────────────────────
client = MongoClient(MONGO_URI)
db = client[DB_NAME]


# ─────────────────────────────────────────────
# COLLECTIONS
# ─────────────────────────────────────────────
users = db.users
payments = db.payments
sessions = db.sessions
settings = db.settings
queue_logs = db.queue_logs


# ─────────────────────────────────────────────
# USER ACCESS
# ─────────────────────────────────────────────
def grant_access(user_id: int, hours: int):
    expiry = datetime.utcnow() + timedelta(hours=hours)
    users.update_one(
        {"user_id": user_id},
        {"$set": {"expiry": expiry}},
        upsert=True
    )


def has_active_access(user_id: int) -> bool:
    user = users.find_one({"user_id": user_id})
    if not user:
        return False
    return user["expiry"] > datetime.utcnow()


def get_access_info(user_id: int) -> str:
    user = users.find_one({"user_id": user_id})
    if not user:
        return "💔 No active access."

    remaining = user["expiry"] - datetime.utcnow()
    hours = remaining.seconds // 3600
    minutes = (remaining.seconds % 3600) // 60

    return f"⏳ Access remaining: **{hours}h {minutes}m**"


# ─────────────────────────────────────────────
# PAYMENT REQUESTS
# ─────────────────────────────────────────────
def create_payment_request(user_id: int, hours: int):
    payments.insert_one({
        "user_id": user_id,
        "hours": hours,
        "status": "pending",
        "created_at": datetime.utcnow()
    })


def approve_payment(user_id: int):
    req = payments.find_one_and_update(
        {"user_id": user_id, "status": "pending"},
        {"$set": {"status": "approved"}},
    )
    if req:
        grant_access(user_id, req["hours"])
        return True
    return False


# ─────────────────────────────────────────────
# SETTINGS (LOG / SESSION GROUP)
# ─────────────────────────────────────────────
def set_setting(key: str, value: int):
    settings.update_one(
        {"key": key},
        {"$set": {"value": value}},
        upsert=True
    )


def get_setting(key: str):
    data = settings.find_one({"key": key})
    return data["value"] if data else None


# ─────────────────────────────────────────────
# SESSION STORAGE
# ─────────────────────────────────────────────
def add_session(session_string: str):
    sessions.insert_one({
        "session": session_string,
        "active": True,
        "added_at": datetime.utcnow()
    })


def list_sessions():
    return list(sessions.find())


def toggle_session(session_id):
    sess = sessions.find_one({"_id": session_id})
    if not sess:
        return False
    sessions.update_one(
        {"_id": session_id},
        {"$set": {"active": not sess["active"]}}
    )
    return True
