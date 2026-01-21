from pyrogram import Client

from config import Config

bot = Client(
    "PreBanBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    in_memory=True,
)
