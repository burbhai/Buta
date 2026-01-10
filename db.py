from pymongo import MongoClient
from datetime import datetime, timedelta
from config import MONGO_URI, DB_NAME

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

users = db["users"]
payments = db["payments"]

# ─────────────────────────────────────────────
# PAYMENT MANAGEMENT
# ─────────────────────────────────────────────
def create_payment_request(user_id: int, hours: int):
    payments.update_one(
        {"user_id": user_id, "status": "pending"},
        {"$set": {"user_id": user_id, "hours": hours, "status": "pending", "created_at": datetime.utcnow()}},
        upsert=True
    )

def approve_payment(user_id: int) -> bool:
    result = payments.find_one({"user_id": user_id, "status": "pending"})
    if not result:
        return False
    payments.update_one({"user_id": user_id, "status": "pending"}, {"$set": {"status": "approved"}})
    grant_user_access(user_id, result["hours"])
    return True

# ─────────────────────────────────────────────
# ACCESS MANAGEMENT
# ─────────────────────────────────────────────
def grant_user_access(user_id: int, hours: int):
    expiry = datetime.utcnow() + timedelta(hours=hours)
    users.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "expiry": expiry}},
        upsert=True
    )
