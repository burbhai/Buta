import asyncio
from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN
from handlers import register_handlers
from core import start_worker

app = Client(
    "preban-bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True
)

register_handlers(app)

async def main():
    await app.start()
    start_worker(app)
    print("✅ Pre-Ban Bot Running")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
