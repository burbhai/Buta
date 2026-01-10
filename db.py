from pymongo import MongoClient
from config import MONGO_URI, DB_NAME
from datetime import datetime, timedelta

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_col = db["users"]

# ─────────────────────────────────────────────
# Create or update user access
# ─────────────────────────────────────────────
def grant_user_access(user_id: int, hours: int):
    expiry = datetime.utcnow() + timedelta(hours=hours)
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "expiry": expiry}},
        upsert=True
    )
    return expiry

# ─────────────────────────────────────────────
# Record payment screenshot request
# ─────────────────────────────────────────────
def record_payment_request(user_id: int, file_id: str, plan_hours: int):
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {
            "payment_file_id": file_id,
            "plan_hours": plan_hours,
            "status": "pending",
            "requested_at": datetime.utcnow()
        }},
        upsert=True
    )

# ─────────────────────────────────────────────
# Approve user payment
# ─────────────────────────────────────────────
def approve_payment(user_id: int):
    user = users_col.find_one({"user_id": user_id, "status": "pending"})
    if not user:
        return False

    hours = user.get("plan_hours", 6)
    expiry = grant_user_access(user_id, hours)
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"status": "approved", "expiry": expiry}}
    )
    return hours

# ─────────────────────────────────────────────
# Get log group from DB
# ─────────────────────────────────────────────
def get_log_group():
    setting = db["settings"].find_one({"key": "log_group"})
    return setting.get("value") if setting else None

def set_log_group(chat_id: int):
    db["settings"].update_one({"key": "log_group"}, {"$set": {"value": chat_id}}, upsert=True)
