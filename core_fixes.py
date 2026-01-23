from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import ReturnDocument
from pyrogram import Client, utils as pyrogram_utils

from db import _users

LOGGER = logging.getLogger(__name__)

_HASH_RE = re.compile(r"^[a-fA-F0-9]{32,}$")


def patch_pyrogram_peer_type() -> None:
    """Patch Pyrogram peer type detection for resilient ID handling."""
    if getattr(pyrogram_utils, "_buta_peer_type_patched", False):
        return

    def _patched_get_peer_type(peer_id: Any) -> str:
        try:
            if isinstance(peer_id, int):
                if peer_id >= 0:
                    return "user"
                if str(peer_id).startswith("-100"):
                    return "channel"
                return "chat"
            if isinstance(peer_id, str):
                if not peer_id:
                    return "chat"
                if peer_id.startswith("-100"):
                    return "channel"
                try:
                    numeric_peer = int(peer_id)
                except ValueError:
                    return "chat"
                if numeric_peer >= 0:
                    return "user"
                return "chat"
        except Exception:
            return "chat"
        return "chat"

    pyrogram_utils.get_peer_type = _patched_get_peer_type
    pyrogram_utils._buta_peer_type_patched = True


def normalize_chat_id(value: Any) -> Optional[int | str]:
    """Normalize a chat_id for safe messaging."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        raw = str(value).strip()
    except Exception:
        return None
    if not raw:
        return None
    lowered = raw.lower()
    if lowered.startswith(("tgid:", "uname:")):
        return None
    if _HASH_RE.fullmatch(lowered):
        return None
    if raw.lstrip("-").isdigit():
        try:
            return int(raw)
        except ValueError:
            return None
    return raw


async def safe_send_message(
    bot: Client,
    chat_id: Any,
    text: str,
    **kwargs: Any,
) -> bool:
    """Send a message with hardened normalization and error handling."""
    normalized = normalize_chat_id(chat_id)
    if normalized is None:
        LOGGER.warning("safe_send_message rejected chat_id=%r", chat_id)
        return False
    try:
        await bot.send_message(normalized, text, **kwargs)
        return True
    except ValueError as exc:
        LOGGER.warning("safe_send_message invalid peer: chat_id=%r error=%s", chat_id, exc)
    except Exception:
        LOGGER.exception("safe_send_message failed for chat_id=%r", chat_id)
    return False


class UserStore:
    """Mongo-backed user storage with preban support."""

    def __init__(self, collection: Any | None = None) -> None:
        self._collection = collection or _users()

    @staticmethod
    def _build_user_key(target: int | str) -> tuple[str, dict[str, Any]]:
        if isinstance(target, int):
            user_key = f"tgid:{target}"
            profile = {"tg_id": int(target), "username": None, "placeholder": True}
            return user_key, profile
        username = str(target).strip().lstrip("@").lower()
        if not username:
            raise ValueError("username is empty")
        user_key = f"uname:{username}"
        profile = {"tg_id": None, "username": username, "placeholder": True}
        return user_key, profile

    async def ensure_user_exists(self, target: int | str) -> dict[str, Any]:
        """Ensure a user document exists, creating a placeholder if missing."""
        user_key, profile = self._build_user_key(target)
        doc_id = hashlib.sha256(user_key.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        update = {
            "$setOnInsert": {
                "_id": doc_id,
                "user_key": user_key,
                "created_at": now,
                "profile": profile,
                "ban": {"prebanned": False, "banned": False},
            }
        }
        return await self._collection.find_one_and_update(
            {"_id": doc_id},
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

    async def preban_user(self, target: int | str, payload: dict[str, Any]) -> dict[str, Any]:
        """Mark a user as prebanned and store metadata."""
        user_doc = await self.ensure_user_exists(target)
        user_key = user_doc["user_key"]
        now = datetime.now(timezone.utc)
        update = {
            "$set": {
                "ban.prebanned": True,
                "ban.preban_reason": payload.get("preban_reason"),
                "ban.preban_by": payload.get("preban_by"),
                "ban.preban_at": payload.get("preban_at", now),
                "ban.preban_until": payload.get("preban_until"),
                "ban.preban_meta": payload.get("preban_meta"),
            }
        }
        return await self._collection.find_one_and_update(
            {"user_key": user_key},
            update,
            return_document=ReturnDocument.AFTER,
        )
