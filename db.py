from pymongo import MongoClient
from config import MONGO_URI, DB_NAME
from datetime import datetime, timedelta

# ─────────────────────────────────────────────
# MongoDB Connection
# ─────────────────────────────────────────────
client = MongoClient(MONGO_URI)
db = client[DB_NAME]

# ─────────────────────────────────────────────
# USERS / PAYMENTS MANAGEMENT
# ─────────────────────────────────────────────

def create_payment_request(user_id: int, hours: int):
    """
    Create a new payment request.
    approved = False by default
    """
    db.payments.insert_one({
        "user_id": user_id,
        "hours": hours,
        "timestamp": datetime.utcnow(),
        "approved": False
    })

def approve_payment(user_id: int) -> bool:
    """
    Mark a payment as approved.
    Returns True if a pending request existed and was approved.
    """
    payment = db.payments.find_one_and_update(
        {"user_id": user_id, "approved": False},
        {"$set": {"approved": True, "approved_at": datetime.utcnow()}}
    )
    return payment is not None

def get_pending_payments():
    """
    Returns list of pending payment requests
    """
    return list(db.payments.find({"approved": False}))

def get_user_access(user_id: int):
    """
    Returns user document (if any)
    """
    return db.users.find_one({"user_id": user_id})

def grant_user_access(user_id: int, hours: int):
    """
    Grant access by storing expiry in users collection
    """
    expiry_time = datetime.utcnow() + timedelta(hours=hours)
    db.users.update_one(
        {"user_id": user_id},
        {"$set": {"expiry": expiry_time}},
        upsert=True
    )
