import logging
import sys
import signal
from pyrogram import Client, idle
from pyrogram.errors import RPCError
import handlers, core
from session_loader import register_session_handler
from payment_handler import register_payment_handler
from queue_worker import start_queue_monitor
from config import API_ID, API_HASH, BOT_TOKEN, DEBUG

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("StartLove")

def create_app() -> Client:
    return Client(
        name="startlove_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workers=50,
        in_memory=True
    )

def shutdown_handler(signum, frame):
    logger.warning(f"Received signal {signum}, shutting down...")
    sys.exit(0)

def main():
    logger.info("Starting StartLove Bot...")
    app = create_app()
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    try:
        handlers.register(app)
        register_session_handler(app)
        register_payment_handler(app)
        core.start_worker(app)
        start_queue_monitor(app)
        app.start()
        logger.info("Bot started successfully")
        idle()
    except KeyboardInterrupt:
        logger.warning("Bot stopped manually")
    except RPCError as e:
        logger.error(f"Telegram RPC error: {e}")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
    finally:
        if app:
            app.stop()
            logger.info("Bot stopped gracefully")

if __name__ == "__main__":
    main()
