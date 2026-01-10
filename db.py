from motor.motor_asyncio import AsyncIOMotorClient
from config import Config
from datetime import datetime, timedelta

client = AsyncIOMotorClient(Config.MONGO_URI)
db = client.preban_db

# Collections
users = db.users
sessions = db.sessions
settings = db.settings

async def get_settings():
    return await settings.find_one({"id": "bot_config"}) or {}

async def update_setting(key, value):
    await settings.update_one({"id": "bot_config"}, {"$set": {key: value}}, upsert=True)

async def add_session(session_str, name, phone):
    await sessions.update_one(
        {"phone": phone},
        {"$set": {"string": session_str, "name": name, "active": True}},
        upsert=True
    )

async def get_active_sessions():
    return await sessions.find({"active": True}).to_list(length=None)

async def give_access(user_id, hours):
    expiry = datetime.utcnow() + timedelta(hours=hours)
    await users.update_one({"user_id": user_id}, {"$set": {"expiry": expiry}}, upsert=True)
