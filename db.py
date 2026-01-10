from pymongo import MongoClient
from datetime import datetime, timedelta
from config import MONGO_URI, DB_NAME

# ─────────────────────────────────────────────
# INITIALIZE MONGO CONNECTION
# ─────────────────────────────────────────────
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

users = db["users"]          # Stores user access and payments
settings = db["settings"]    # Stores log group, session group, etc.

# ─────────────────────────────────────────────
# USER ACCESS MANAGEMENT
# ─────────────────────────────────────────────
def grant_access(user_id: int, hours: int):
    expiry_time = datetime.utcnow() + timedelta(hours=hours)
    users.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "expiry": expiry_time}},
        upsert=True
    )

def has_active_access(user_id: int) -> bool:
    record = users.find_one({"user_id": user_id})
    if not record or "expiry" not in record:
        return False
    return datetime.utcnow() < record["expiry"]

def get_access_info(user_id: int) -> str:
    record = users.find_one({"user_id": user_id})
    if not record or "expiry" not in record:
        return "💔 You don’t have any active access."
    remaining = record["expiry"] - datetime.utcnow()
    hrs = remaining.seconds // 3600
    mins = (remaining.seconds % 3600) // 60
    return f"💼 Access remaining: {hrs}h {mins}m"

# ─────────────────────────────────────────────
# PAYMENT MANAGEMENT
# ─────────────────────────────────────────────
def create_payment_request(user_id: int, hours: int):
    users.update_one(
        {"user_id": user_id},
        {"$set": {"pending_payment": True, "requested_hours": hours, "timestamp": datetime.utcnow()}},
        upsert=True
    )

def approve_payment(user_id: int) -> bool:
    record = users.find_one({"user_id": user_id})
    if not record or not record.get("pending_payment"):
        return False
    hours = record.get("requested_hours", 6)
    grant_access(user_id, hours)
    users.update_one({"user_id": user_id}, {"$unset": {"pending_payment": "", "requested_hours": ""}})
    return True

# ─────────────────────────────────────────────
# SETTINGS MANAGEMENT (LOG GROUP, SESSION GROUP)
# ─────────────────────────────────────────────
def set_setting(key: str, value):
    settings.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

def get_setting(key: str):
    record = settings.find_one({"key": key})
    if record:
        return record.get("value")
    return None
