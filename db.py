from motor.motor_asyncio import AsyncIOMotorClient
from config import Config
from datetime import datetime, timedelta

client = AsyncIOMotorClient(Config.MONGO_URI)
db = client[Config.DB_NAME]

# Collections
users = db.users
sessions = db.sessions
settings = db.settings
user_cache = db.user_cache

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

async def deactivate_session(phone):
    await sessions.update_one({"phone": phone}, {"$set": {"active": False}})

async def give_access(user_id, hours):
    expiry = datetime.utcnow() + timedelta(hours=hours)
    await users.update_one({"user_id": user_id}, {"$set": {"expiry": expiry}}, upsert=True)

async def has_access(user_id):
    try:
        user = await users.find_one({"user_id": user_id})
    except Exception:
        return False
    if not user:
        return False
    expiry = user.get("expiry")
    if not expiry:
        return False
    return expiry > datetime.utcnow()


def _normalize_username(username):
    if not username:
        return None
    return str(username).lower().lstrip("@")


async def get_user_cache(*, user_id=None, username=None):
    if user_id is None and not username:
        return None
    normalized_username = _normalize_username(username)
    query = {}
    if user_id is not None and normalized_username:
        query = {"$or": [{"user_id": int(user_id)}, {"username_norm": normalized_username}]}
    elif user_id is not None:
        query = {"user_id": int(user_id)}
    else:
        query = {"username_norm": normalized_username}
    return await user_cache.find_one(query)


async def upsert_user_cache(user):
    normalized_username = _normalize_username(user.get("username"))
    payload = {
        "user_id": int(user["user_id"]),
        "access_hash": int(user["access_hash"]),
        "username": user.get("username"),
        "username_norm": normalized_username,
        "updated_at": int(user.get("updated_at") or datetime.utcnow().timestamp()),
    }
    await user_cache.update_one(
        {"user_id": payload["user_id"]},
        {"$set": payload},
        upsert=True,
    )
