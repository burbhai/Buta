import logging
import sys
import signal
import asyncio

from pyrogram import Client, idle
from pyrogram.errors import RPCError

from config import API_ID, API_HASH, BOT_TOKEN, DEBUG
import handlers
import core
from session_loader import register_session_handler
from payment_handler import register_payment_handler
from queue_handlers import start_queue_monitor

# ─────────────────────────────────────────────
# LOGGING CONFIGURATION
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("StartLove")


# ─────────────────────────────────────────────
# CREATE PYROGRAM BOT CLIENT
# ─────────────────────────────────────────────
def create_app() -> Client:
    return Client(
        name="startlove_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workers=50,
        in_memory=True  # safe for Heroku (no local session file)
    )


# ─────────────────────────────────────────────
# GRACEFUL SHUTDOWN HANDLER
# ─────────────────────────────────────────────
def shutdown_handler(signum, frame):
    logger.warning(f"Received signal {signum}, shutting down...")
    try:
        asyncio.get_event_loop().stop()
    except Exception:
        pass
    sys.exit(0)


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────
def main():
    logger.info("Initializing StartLove Bot...")

    app = create_app()

    # Register OS signals for graceful shutdown
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        # Register handlers
        handlers.register(app)
        register_session_handler(app)       # multi-session pre-ban
        register_payment_handler(app)       # payment verification
        logger.info("Handlers registered")

        # Start background workers
        core.start_worker(app)              # main queue worker (pre-ban)
        start_queue_monitor(app)            # optional monitoring thread
        logger.info("Background workers started")

        # Start bot in polling mode
        app.start()
        logger.info("Bot started successfully (Polling mode)")

        # Keep bot running
        idle()

    except KeyboardInterrupt:
        logger.warning("Bot stopped manually")

    except RPCError as e:
        logger.error(f"Telegram RPC error: {e}")

    except Exception as e:
        logger.exception(f"Unexpected fatal error: {e}")

    finally:
        try:
            # Stop bot gracefully
            if app:
                app.stop()
                logger.info("Bot stopped gracefully")
        except Exception:
            pass


# ─────────────────────────────────────────────
if __name__ == "__main__":
    main()
