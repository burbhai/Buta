import asyncio
from pyrogram import Client
from database import get_active_sessions, get_settings
from config import Config

ban_queue = asyncio.Queue()

async def pre_ban_worker(bot):
    while True:
        target, requester_id = await ban_queue.get()
        conf = await get_settings()
        log_group = conf.get("log_group")
        
        all_sessions = await get_active_sessions()
        success_count = 0
        
        for s in all_sessions:
            try:
                # Use in_memory to avoid creating .session files on Heroku
                agent = Client("agent", session_string=s['string'], 
                               api_id=Config.API_ID, api_hash=Config.API_HASH, in_memory=True)
                await agent.start()
                
                async for dialog in agent.get_dialogs():
                    if dialog.chat.type in ["group", "supergroup", "channel"]:
                        try:
                            # Ban target (works even if user is not in group)
                            await agent.ban_chat_member(dialog.chat.id, target)
                            success_count += 1
                        except Exception:
                            continue
                await agent.stop()
            except Exception:
                continue

        if log_group:
            await bot.send_message(log_group, f"🛡 **Pre-Ban Done**\nTarget: `{target}`\nTotal Bans: {success_count}\nBy: `{requester_id}`")
        
        await bot.send_message(requester_id, f"✅ Pre-ban finished for `{target}` across {success_count} groups.")
        ban_queue.task_done()
        await asyncio.sleep(2) # Cooldown
