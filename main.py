import logging
import sys
import signal

from pyrogram import Client, idle
from pyrogram.errors import RPCError

from config import API_ID, API_HASH, BOT_TOKEN, DEBUG
import handlers
import core
from session_handler import register_session_handler
from payment_handler import register_payment_handler
from queue_worker import start_queue_monitor

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
# CREATE PYROGRAM CLIENT (POLLING MODE)
# ─────────────────────────────────────────────
def create_app() -> Client:
    return Client(
        name="startlove_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workers=50,        # good for buttons + messages
        in_memory=True     # Heroku safe (no local session file)
    )


# ─────────────────────────────────────────────
# GRACEFUL SHUTDOWN HANDLER
# ─────────────────────────────────────────────
def shutdown_handler(signum, frame):
    logger.warning(f"Received signal {signum}, shutting down...")
    sys.exit(0)


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────
def main():
    logger.info("Initializing StartLove Bot...")

    app = create_app()

    # Heroku / manual stop safe
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        # Register all Telegram handlers
        handlers.register(app)
        register_session_handler(app)
        register_payment_handler(app)
        logger.info("Handlers registered")

        # Start background queue worker thread
        core.start_worker(app)            # main pre-ban queue worker
        start_queue_monitor(app)          # optional monitoring thread
        logger.info("Background worker started")

        # Start bot (LONG POLLING)
        app.start()
        logger.info("Bot started successfully (Polling mode)")

        # Keep bot alive
        idle()

    except KeyboardInterrupt:
        logger.warning("Bot stopped manually")

    except RPCError as e:
        logger.error(f"Telegram RPC error: {e}")

    except Exception as e:
        logger.exception(f"Unexpected fatal error: {e}")

    finally:
        try:
            app.stop()
            logger.info("Bot stopped gracefully")
        except Exception:
            pass


if __name__ == "__main__":
    main()
