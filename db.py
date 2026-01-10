from pymongo import MongoClient
from config import MONGO_URI
import time

mongo = MongoClient(MONGO_URI)
db = mongo["preban_bot"]

users = db.users
sessions = db.sessions
payments = db.payments
settings = db.settings


def get_setting(key, default=None):
    s = settings.find_one({"_id": key})
    return s["value"] if s else default


def set_setting(key, value):
    settings.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)


def grant_access(user_id, seconds):
    expiry = int(time.time()) + seconds
    users.update_one(
        {"user_id": user_id},
        {"$set": {"expiry": expiry, "active": True}},
        upsert=True
    )


def has_access(user_id):
    u = users.find_one({"user_id": user_id})
    return u and u.get("active") and u.get("expiry", 0) > time.time()
