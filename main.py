import logging, sys, signal, asyncio
from pyrogram import Client, idle
from pyrogram.errors import RPCError

from config import API_ID, API_HASH, BOT_TOKEN, DEBUG
import handlers
import core
from session_loader import register_session_handler
from payment_handler import register_payment_handler
from queue_handler import start_queue_monitor  # monitoring thread

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("StartLove")

# ─────────────────────────────────────────────
# CREATE BOT
# ─────────────────────────────────────────────
def create_app() -> Client:
    return Client(
        name="startlove_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workers=50,
        in_memory=True  # Heroku-safe
    )

# ─────────────────────────────────────────────
# GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────
def shutdown_handler(signum, frame):
    logger.warning(f"Received signal {signum}, shutting down...")
    try: asyncio.get_event_loop().stop()
    except: pass
    sys.exit(0)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    logger.info("Initializing StartLove Bot...")

    app = create_app()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        # Register handlers
        handlers.register(app)
        register_session_handler(app)
        register_payment_handler(app)
        logger.info("Handlers registered")

        # Start workers
        core.start_worker(app)           # queue worker
        start_queue_monitor(app)         # optional monitor
        logger.info("Background workers started")

        # Start bot
        app.start()
        logger.info("Bot started successfully (Polling mode)")
        idle()

    except KeyboardInterrupt:
        logger.warning("Bot stopped manually")

    except RPCError as e:
        logger.error(f"Telegram RPC error: {e}")

    except Exception as e:
        logger.exception(f"Unexpected fatal error: {e}")

    finally:
        try:
            if app:
                app.stop()
                logger.info("Bot stopped gracefully")
        except:
            pass

if __name__=="__main__":
    main()
