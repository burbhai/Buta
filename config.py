import os

# ─────────────────────────────────────────────
# TELEGRAM CORE CONFIG
# ─────────────────────────────────────────────
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ─────────────────────────────────────────────
# OWNER CONFIG
# ENV FORMAT: OWNER_IDS=123456789,987654321
# ─────────────────────────────────────────────
OWNER_IDS = [
    int(x.strip())
    for x in os.getenv("OWNER_IDS", "").split(",")
    if x.strip().isdigit()
]

# ─────────────────────────────────────────────
# DATABASE CONFIG (MongoDB Atlas)
# ─────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "startlove")

# ─────────────────────────────────────────────
# ACCESS PLANS (HOURS BASED)
# ─────────────────────────────────────────────
ACCESS_PLANS = {
    "6h": 6,
    "12h": 12,
    "18h": 18,
    "24h": 24,
    "48h": 48
}

# ─────────────────────────────────────────────
# QUEUE & WORKER SETTINGS
# ─────────────────────────────────────────────
QUEUE_CHECK_DELAY = int(os.getenv("QUEUE_CHECK_DELAY", 2))  # seconds
TASK_COOLDOWN = int(os.getenv("TASK_COOLDOWN", 5))          # seconds between tasks

# ─────────────────────────────────────────────
# RUNTIME FLAGS
# ─────────────────────────────────────────────
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# ─────────────────────────────────────────────
# SESSION GROUP (Dynamic, set by /set_session)
# ─────────────────────────────────────────────
SESSION_GROUP_ID = None
