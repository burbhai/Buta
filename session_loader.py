import asyncio
from pyrogram import Client
from pyrogram.errors import RPCError
from db import sessions
from config import API_ID, API_HASH


async def validate_session(session_string: str) -> bool:
    try:
        async with Client(
            name="check",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True
        ) as app:
            await app.get_me()
        return True
    except RPCError:
        return False


async def save_session(session_string: str):
    if await validate_session(session_string):
        sessions.update_one(
            {"session": session_string},
            {"$set": {"active": True}},
            upsert=True
        )
        return True
    return False
