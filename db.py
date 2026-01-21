from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient

from config import Config

LOGGER = logging.getLogger(__name__)

client = AsyncIOMotorClient(Config.MONGO_URI)
db = client[Config.DB_NAME]

# Collections
users = db.users
sessions = db.sessions
settings = db.settings
user_cache = db.user_cache


async def ensure_indexes() -> None:
    """Ensure MongoDB indexes needed for bot queries."""
    try:
        await sessions.create_index("active")
        await user_cache.create_index("username_norm")
        await user_cache.create_index("user_id")
        await users.create_index("expiry")
    except Exception:
        LOGGER.exception("Failed to create MongoDB indexes.")


async def check_db_health() -> bool:
    """Ping MongoDB to confirm connectivity at startup."""
    try:
        await client.admin.command("ping")
        LOGGER.info("MongoDB connectivity check: OK.")
        return True
    except Exception:
        LOGGER.exception("MongoDB connectivity check failed.")
        return False

async def get_settings() -> Dict[str, Any]:
    """Return bot settings document."""
    return await settings.find_one({"id": "bot_config"}) or {}

async def update_setting(key: str, value: Any) -> None:
    """Update a single settings key."""
    await settings.update_one({"id": "bot_config"}, {"$set": {key: value}}, upsert=True)

async def add_session(session_str: str, name: str, phone: str) -> None:
    """Add or update a user session."""
    await sessions.update_one(
        {"phone": phone},
        {"$set": {"string": session_str, "name": name, "phone": phone, "active": True}},
        upsert=True,
    )

async def get_active_sessions() -> list[Dict[str, Any]]:
    """Return active sessions."""
    return await sessions.find({"active": True}).to_list(length=None)

async def deactivate_session(phone: str) -> None:
    """Deactivate a session by phone."""
    await sessions.update_one({"phone": phone}, {"$set": {"active": False}})

async def give_access(user_id: int, hours: int) -> None:
    """Grant sudo access for a number of hours."""
    expiry = datetime.utcnow() + timedelta(hours=hours)
    await users.update_one({"user_id": user_id}, {"$set": {"expiry": expiry}}, upsert=True)

async def revoke_access(user_id: int) -> None:
    """Revoke sudo access immediately."""
    await users.update_one({"user_id": user_id}, {"$set": {"expiry": datetime.utcnow()}}, upsert=True)

async def get_active_sudo_users() -> list[Dict[str, Any]]:
    """Return users with active sudo access."""
    now = datetime.utcnow()
    cursor = users.find({"expiry": {"$gt": now}})
    return await cursor.to_list(length=None)

async def has_access(user_id: int) -> bool:
    """Check if the user has active sudo access."""
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


def _normalize_username(username: Optional[str]) -> Optional[str]:
    if not username:
        return None
    return str(username).lower().lstrip("@")


async def get_user_cache(*, user_id: Optional[int] = None, username: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get cached user access_hash entry by user_id or username."""
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


async def upsert_user_cache(user: Dict[str, Any]) -> None:
    """Upsert cached user entry for access_hash lookup."""
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
