from pymongo import MongoClient
from config import MONGO_URI, DB_NAME
from datetime import datetime, timedelta

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_col = db["users"]
settings_col = db["settings"]

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
def set_setting(key: str, value):
    settings_col.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

def get_setting(key: str):
    doc = settings_col.find_one({"key": key})
    return doc["value"] if doc else None

# ─────────────────────────────────────────────
# PAYMENTS
# ─────────────────────────────────────────────
def create_payment_request(user_id: int, hours: int):
    """Create a payment request (user sends screenshot later)"""
    expiry_time = datetime.utcnow() + timedelta(hours=hours)
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"status": "pending", "expiry": expiry_time}},
        upsert=True
    )

def approve_payment(user_id: int) -> bool:
    """Admin approves payment, returns True if success"""
    user = users_col.find_one({"user_id": user_id})
    if not user or user.get("status") != "pending":
        return False
    users_col.update_one({"user_id": user_id}, {"$set": {"status": "approved"}})
    return True

def get_user_status(user_id: int):
    user = users_col.find_one({"user_id": user_id})
    return user.get("status") if user else None

def get_user_expiry(user_id: int):
    user = users_col.find_one({"user_id": user_id})
    return user.get("expiry") if user else None
