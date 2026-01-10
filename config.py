import os

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

MONGO_URI = os.getenv("MONGO_URI")

OWNER_IDS = list(map(int, os.getenv("OWNER_IDS", "").split()))

QUEUE_DELAY = int(os.getenv("QUEUE_DELAY", 3))
TASK_COOLDOWN = int(os.getenv("TASK_COOLDOWN", 1))

DEFAULT_PLANS = {
    "6h": 6 * 60 * 60,
    "12h": 12 * 60 * 60,
    "24h": 24 * 60 * 60
}
