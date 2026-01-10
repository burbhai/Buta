from pymongo import MongoClient
from datetime import datetime, timedelta
from config import MONGO_URI, DB_NAME

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# ─────────────────────────────────────────────
# USERS COLLECTION
# ─────────────────────────────────────────────
users = db["users"]

def create_payment_request(user_id: int, hours: int):
    users.update_one(
        {"user_id": user_id},
        {"$set": {"pending_hours": hours, "pending": True, "created_at": datetime.utcnow()}},
        upsert=True
    )

def approve_payment(user_id: int):
    user = users.find_one({"user_id": user_id, "pending": True})
    if not user:
        return False

    hours = user.get("pending_hours", 6)
    expiry = datetime.utcnow() + timedelta(hours=hours)

    users.update_one(
        {"user_id": user_id},
        {"$set": {"expiry": expiry, "pending": False}, "$unset": {"pending_hours": ""}}
    )
    return True

def get_setting(key: str):
    settings = db["settings"]
    s = settings.find_one({"key": key})
    return s["value"] if s else None

def set_setting(key: str, value):
    settings = db["settings"]
    settings.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)
