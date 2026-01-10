from pymongo import MongoClient
from datetime import datetime, timedelta
from config import MONGO_URI, DB_NAME

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

users_col = db["users"]
payments_col = db["payments"]

# Payment request
def create_payment_request(user_id: int, hours: int):
    payments_col.insert_one({
        "user_id": user_id,
        "hours": hours,
        "timestamp": datetime.utcnow(),
        "approved": False
    })

# Approve payment
def approve_payment(user_id: int) -> bool:
    doc = payments_col.find_one({"user_id": user_id, "approved": False})
    if not doc: return False
    payments_col.update_one({"_id": doc["_id"]}, {"$set":{"approved": True}})
    expiry = datetime.utcnow() + timedelta(hours=doc["hours"])
    users_col.update_one({"user_id": user_id}, {"$set":{"expiry": expiry}}, upsert=True)
    return True

# Check user expiry
def get_user_expiry(user_id: int):
    doc = users_col.find_one({"user_id": user_id})
    if doc: return doc.get("expiry")
    return None
