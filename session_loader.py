from __future__ import annotations

import logging
import uuid

from pyrogram import Client
from pyrogram.errors import RPCError

from config import Config
from db import add_session

LOGGER = logging.getLogger(__name__)


async def validate_session(session_string: str) -> bool:
    """Validate a Pyrogram session string by calling get_me()."""
    try:
        async with Client(
            name=f"session_check_{uuid.uuid4().hex}",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=session_string,
        ) as app:
            await app.get_me()
        return True
    except RPCError:
        return False


async def save_session(session_string: str) -> bool:
    """Validate and upsert a session as active."""
    me = None
    try:
        async with Client(
            name=f"session_save_{uuid.uuid4().hex}",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=session_string,
        ) as app:
            me = await app.get_me()
        await add_session(session_string, me.first_name, me.phone_number or str(me.id))
        return True
    except RPCError:
        identifier = None
        if me:
            identifier = me.phone_number or str(me.id)
        LOGGER.error("RPC error while saving session for %s.", identifier or "unknown user")
        return False
    except Exception:
        LOGGER.exception("Failed to save session.")
        return False


async def test_all_sessions() -> None:
    """Validate existing sessions and deactivate invalid ones."""
    from db import deactivate_session, get_active_sessions

    sessions = await get_active_sessions()
    if not sessions:
        LOGGER.info("No active sessions found for validation.")
        return
    for row in sessions:
        session_string = row.get("string")
        phone = row.get("phone") or row.get("name") or "unknown"
        if not session_string:
            LOGGER.warning("Session missing string for %s.", phone)
            continue
        ok = await validate_session(session_string)
        if not ok:
            LOGGER.warning("Deactivating invalid session for %s.", phone)
            await deactivate_session(phone)
