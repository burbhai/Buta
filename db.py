from pymongo import MongoClient
from config import MONGO_URI, DB_NAME
from datetime import datetime, timedelta

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

users = db.users
payments = db.payments
settings = db.settings

def create_payment_request(user_id: int, hours: int):
    payments.insert_one({
        "user_id": user_id,
        "hours": hours,
        "status": "pending",
        "created_at": datetime.utcnow()
    })

def approve_payment(user_id: int):
    payment = payments.find_one({"user_id": user_id, "status": "pending"})
    if not payment: return False
    payments.update_one({"_id": payment["_id"]},{"$set":{"status":"approved"}})
    expiry = datetime.utcnow() + timedelta(hours=payment["hours"])
    users.update_one({"user_id":user_id},{"$set":{"expiry":expiry}},upsert=True)
    return True

def get_setting(key: str):
    rec = settings.find_one({"key":key})
    return rec["value"] if rec else None

def set_setting(key: str, value):
    settings.update_one({"key":key},{"$set":{"value":value}},upsert=True)
