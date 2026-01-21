import os
import re


class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    _owner_raw = os.getenv("OWNER_IDS", "")
    OWNERS = [int(x) for x in re.split(r"[,\s]+", _owner_raw.strip()) if x]
    MONGO_URI = os.getenv("MONGO_URI", "")
    DB_NAME = os.getenv("DB_NAME", "preban_db")
