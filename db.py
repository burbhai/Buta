from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorDatabase
import pymongo.errors as pymongo_errors

from config import Config

LOGGER = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[Any] = None
IndexOptionsConflict = getattr(
    pymongo_errors,
    "IndexOptionsConflict",
    pymongo_errors.OperationFailure,
)


def _use_in_memory_db(reason: str) -> None:
    """Switch to in-memory DB fallback and warn."""
    global _client, _db
    _client = None
    _db = InMemoryDB()
    LOGGER.warning("Using in-memory DB fallback: %s", reason)


class InMemoryCursor:
    """Simple async cursor for in-memory collections."""

    def __init__(self, rows: list[Dict[str, Any]]) -> None:
        self._rows = rows

    async def to_list(self, length: Optional[int] = None) -> list[Dict[str, Any]]:
        return list(self._rows)


class InMemoryCollection:
    """Minimal in-memory collection to emulate async Motor calls."""

    def __init__(self) -> None:
        self._docs: list[Dict[str, Any]] = []

    async def create_index(self, *args, **kwargs) -> None:
        return None

    async def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for doc in self._docs:
            if _match_query(doc, query):
                return dict(doc)
        return None

    def find(self, query: Dict[str, Any]) -> InMemoryCursor:
        rows = [dict(doc) for doc in self._docs if _match_query(doc, query)]
        return InMemoryCursor(rows)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        for doc in self._docs:
            if _match_query(doc, query):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            payload = dict(update.get("$set", {}))
            for key, value in query.items():
                if isinstance(value, dict) or key == "$or":
                    continue
                payload.setdefault(key, value)
            self._docs.append(payload)

    async def find_one_and_update(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
        return_document: Any = None,
    ) -> Optional[Dict[str, Any]]:
        for doc in self._docs:
            if _match_query(doc, query):
                doc.update(update.get("$set", {}))
                return dict(doc)
        if upsert:
            payload = {}
            for key, value in query.items():
                if isinstance(value, dict) or key == "$or":
                    continue
                payload[key] = value
            payload.update(update.get("$setOnInsert", {}))
            payload.update(update.get("$set", {}))
            self._docs.append(payload)
            return dict(payload)
        return None


class InMemoryDB:
    """In-memory database fallback used when MongoDB is not configured."""

    def __init__(self) -> None:
        self.users = InMemoryCollection()
        self.sessions = InMemoryCollection()
        self.settings = InMemoryCollection()
        self.user_cache = InMemoryCollection()
        self.payments = InMemoryCollection()


def _match_query(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    if not query:
        return True
    for key, value in query.items():
        if key == "$or":
            return any(_match_query(doc, sub) for sub in value)
        if isinstance(value, dict):
            if "$gt" in value:
                if doc.get(key) is None or doc.get(key) <= value["$gt"]:
                    return False
                continue
        if doc.get(key) != value:
            return False
    return True


def _get_client() -> Optional[AsyncIOMotorClient]:
    """Return a MongoDB client or None for in-memory fallback."""
    global _client
    if _client is None:
        if not Config.MONGO_URI:
            LOGGER.warning("MongoDB URI is not configured. Using in-memory DB.")
            return None
        try:
            _client = AsyncIOMotorClient(Config.MONGO_URI)
        except Exception:
            LOGGER.exception("Failed to create MongoDB client. Using in-memory DB.")
            return None
    return _client


def _get_db() -> Any:
    """Return database handle, falling back to in-memory DB."""
    global _db
    if _db is None:
        client = _get_client()
        if client is None:
            _db = InMemoryDB()
        else:
            _db = client[Config.DB_NAME]
    return _db


def _users():
    return _get_db().users


def _sessions():
    return _get_db().sessions


def _settings():
    return _get_db().settings


def _user_cache():
    return _get_db().user_cache


def _payments():
    return _get_db().payments


async def ensure_indexes() -> None:
    """Ensure MongoDB indexes needed for bot queries."""
    try:
        await _sessions().create_index("active")
        await _user_cache().create_index("username_norm")
        await _user_cache().create_index("user_id")
        await _users().create_index("expiry", expireAfterSeconds=0)
        await _users().create_index("user_key", unique=True)
        await _users().create_index("ban.prebanned")
    except IndexOptionsConflict:
        LOGGER.warning("MongoDB index options conflict; continuing without changes.")
    except Exception:
        LOGGER.exception("Failed to create MongoDB indexes.")


async def check_db_health() -> bool:
    """Ping MongoDB to confirm connectivity at startup."""
    if isinstance(_get_db(), InMemoryDB):
        LOGGER.warning("Using in-memory DB fallback.")
        return True
    try:
        client = _get_client()
        if client is None:
            return True
        await client.admin.command("ping")
        LOGGER.info("MongoDB connectivity check: OK.")
        return True
    except Exception:
        LOGGER.exception("MongoDB connectivity check failed.")
        _use_in_memory_db("MongoDB ping failed.")
        return True

async def get_settings() -> Dict[str, Any]:
    """Return bot settings document."""
    return await _settings().find_one({"id": "bot_config"}) or {}

async def update_setting(key: str, value: Any) -> None:
    """Update a single settings key."""
    await _settings().update_one({"id": "bot_config"}, {"$set": {key: value}}, upsert=True)

async def add_session(session_str: str, name: str, phone: str) -> None:
    """Add or update a user session."""
    await _sessions().update_one(
        {"phone": phone},
        {"$set": {"string": session_str, "name": name, "phone": phone, "active": True}},
        upsert=True,
    )

async def get_active_sessions() -> list[Dict[str, Any]]:
    """Return active sessions."""
    return await _sessions().find({"active": True}).to_list(length=None)

async def deactivate_session(phone: str) -> None:
    """Deactivate a session by phone."""
    await _sessions().update_one({"phone": phone}, {"$set": {"active": False}})

async def give_access(user_id: int, hours: int) -> None:
    """Grant sudo access for a number of hours."""
    now = datetime.utcnow()
    existing = await _users().find_one({"user_id": user_id}) or {}
    current_expiry = existing.get("expiry")
    if current_expiry and isinstance(current_expiry, datetime) and current_expiry > now:
        expiry = current_expiry + timedelta(hours=hours)
    else:
        expiry = now + timedelta(hours=hours)
    await _users().update_one({"user_id": user_id}, {"$set": {"expiry": expiry}}, upsert=True)

async def revoke_access(user_id: int) -> None:
    """Revoke sudo access immediately."""
    await _users().update_one({"user_id": user_id}, {"$set": {"expiry": datetime.utcnow()}}, upsert=True)

async def get_active_sudo_users() -> list[Dict[str, Any]]:
    """Return users with active sudo access."""
    now = datetime.utcnow()
    cursor = _users().find({"expiry": {"$gt": now}})
    rows = await cursor.to_list(length=None)
    for row in rows:
        expiry = row.get("expiry")
        if expiry:
            row["remaining_seconds"] = int((expiry - now).total_seconds())
    return rows

async def has_access(user_id: int) -> bool:
    """Check if the user has active sudo access."""
    try:
        user = await _users().find_one({"user_id": user_id})
    except Exception:
        LOGGER.exception("Failed to check access for user_id=%s.", user_id)
        return False
    if not user:
        return False
    expiry = user.get("expiry")
    if not expiry:
        return False
    if expiry <= datetime.utcnow():
        await _users().update_one({"user_id": user_id}, {"$set": {"expiry": datetime.utcnow()}}, upsert=True)
        return False
    return True


async def record_payment_request(user_id: int, chat_id: int, message_id: int) -> None:
    """Record a payment request by forwarded message id."""
    await _payments().update_one(
        {"message_id": message_id},
        {"$set": {"user_id": user_id, "chat_id": chat_id, "status": "pending"}},
        upsert=True,
    )


async def mark_payment_status(message_id: int, status: str, reviewed_by: int | None) -> None:
    """Mark payment approval/rejection status."""
    await _payments().update_one(
        {"message_id": message_id},
        {"$set": {"status": status, "reviewed_by": reviewed_by, "reviewed_at": datetime.utcnow()}},
        upsert=True,
    )


async def get_payment_request(message_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a payment request by forwarded message id."""
    return await _payments().find_one({"message_id": message_id})


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
    return await _user_cache().find_one(query)


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
    await _user_cache().update_one(
        {"user_id": payload["user_id"]},
        {"$set": payload},
        upsert=True,
    )
