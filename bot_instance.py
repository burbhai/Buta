from __future__ import annotations

import logging
from typing import Optional

from pyrogram import Client

from config import Config

_bot: Optional[Client] = None
LOGGER = logging.getLogger(__name__)


def get_bot() -> Client:
    """Return a singleton Pyrogram Client instance."""
    global _bot
    if _bot is None:
        if not Config.API_ID or not Config.API_HASH or not Config.BOT_TOKEN:
            LOGGER.warning("Bot credentials appear missing or incomplete.")
        try:
            _bot = Client(
                "PreBanBot",
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                bot_token=Config.BOT_TOKEN,
            )
            LOGGER.info("Bot client initialized.")
        except Exception:
            LOGGER.exception("Failed to initialize bot client. Check API_ID/API_HASH/BOT_TOKEN.")
            raise
    return _bot
