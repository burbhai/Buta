# session_loader.py
import asyncio
from pyrogram import Client
from core import SESSIONS, LOCK
from config import API_ID, API_HASH, SESSION_GROUP_ID
import logging

logger = logging.getLogger("StartLove.SessionLoader")


async def load_sessions_from_strings(session_strings: list):
    """
    Load multiple Pyrogram clients from session strings.
    Each session is stored in core.SESSIONS as:
    {"id": int, "active": bool, "client": Client}
    """
    for idx, string in enumerate(session_strings):
        try:
            client = Client(
                name=f"session_{idx}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=string,
                in_memory=True  # Heroku safe
            )
            await client.start()
            session_info = {
                "id": idx,
                "active": True,
                "client": client
            }
            with LOCK:
                SESSIONS.append(session_info)
            logger.info(f"✅ Session {idx+1} started successfully")
        except Exception as e:
            logger.error(f"❌ Failed to start session {idx+1}: {e}")


def register_session_handler(app):
    """
    Entry point called from main.py
    - Reads session strings from the SESSION_GROUP (if set)
    - Starts all sessions
    """
    import db  # optional: fetch session strings from DB

    session_strings = []

    # Option 1: Load from DB if SESSION_GROUP_ID is set
    if SESSION_GROUP_ID:
        # Fetch saved session strings from db.sessions
        saved_sessions = db.list_sessions()
        session_strings = [s.get("session") for s in saved_sessions if s.get("session")]

    # Option 2: fallback: env var (if you store them in env)
    # import os
    # env_sessions = os.getenv("SESSION_STRINGS", "")
    # if env_sessions:
    #     session_strings += [s.strip() for s in env_sessions.split(",") if s.strip()]

    # Start all sessions asynchronously
    if session_strings:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(load_sessions_from_strings(session_strings))
    else:
        logger.warning("⚠️ No session strings found. Multi-session pre-ban will not run.")
