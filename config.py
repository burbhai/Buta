import os


class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    OWNERS = [int(x) for x in os.getenv("OWNER_IDS", "").split()]
    MONGO_URI = os.getenv("MONGO_URI", "")
