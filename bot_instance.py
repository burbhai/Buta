from __future__ import annotations

from typing import Optional

from pyrogram import Client

from config import Config

_bot: Optional[Client] = None


def get_bot() -> Client:
    global _bot
    if _bot is None:
        _bot = Client(
            "PreBanBot",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=Config.BOT_TOKEN,
        )
    return _bot


bot = get_bot()
