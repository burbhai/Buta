import os

# TELEGRAM
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# OWNERS
OWNER_IDS = [int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()]

# DATABASE
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "startlove")

# ACCESS PLANS
ACCESS_PLANS = {
    "6h": 6,
    "12h": 12,
    "24h": 24,
    "48h": 48
}

# QUEUE & WORKER
QUEUE_CHECK_DELAY = int(os.getenv("QUEUE_CHECK_DELAY", 2))
TASK_COOLDOWN = int(os.getenv("TASK_COOLDOWN", 5))

# FLAGS
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
