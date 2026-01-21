import asyncio
from pyrogram import Client
from db import get_active_sessions, get_settings
from config import Config

ban_queue = asyncio.Queue()

async def pre_ban_worker(bot):
    while True:
        target_info, requester_id = await ban_queue.get()
        conf = await get_settings()
        log_group = conf.get("log_group")
        
        all_sessions = await get_active_sessions()
        success_count = 0
        attempt_count = 0
        session_count = 0
        
        target_id = None
        target_username = None
        if isinstance(target_info, dict):
            target_id = target_info.get("id")
            target_username = target_info.get("username")
        else:
            target_id = target_info

        for s in all_sessions:
            try:
                # Use in_memory to avoid creating .session files on Heroku
                agent = Client(
                    "agent",
                    session_string=s["string"],
                    api_id=Config.API_ID,
                    api_hash=Config.API_HASH,
                    in_memory=True,
                )
                await agent.start()
                session_count += 1
                resolved_id = target_id
                if resolved_id is None and target_username:
                    try:
                        resolved = await agent.get_users(target_username)
                        resolved_id = resolved.id
                    except Exception:
                        await agent.stop()
                        continue
                if resolved_id is None:
                    await agent.stop()
                    continue
                try:
                    if resolved_id is not None:
                        await agent.get_users(resolved_id)
                except Exception:
                    pass

                async for dialog in agent.get_dialogs():
                    if dialog.chat.type not in ["group", "supergroup"]:
                        continue
                    try:
                        me_member = await agent.get_chat_member(dialog.chat.id, "me")
                    except Exception:
                        continue
                    can_restrict = me_member.status == "creator"
                    if not can_restrict and getattr(me_member, "privileges", None):
                        can_restrict = bool(me_member.privileges.can_restrict_members)
                    if not can_restrict:
                        continue

                    attempt_count += 1
                    try:
                        # Ban target (works even if user is not in group)
                        await agent.ban_chat_member(dialog.chat.id, resolved_id)
                        success_count += 1
                    except Exception:
                        continue
                await agent.stop()
            except Exception:
                continue

        if log_group:
            await bot.send_message(
                log_group,
                "🛡 **Pre-Ban Done**"
                f"\nTarget: `{target_id if target_id is not None else target_username}`"
                f"\nSessions Used: {session_count}"
                f"\nAttempts: {attempt_count}"
                f"\nTotal Bans: {success_count}"
                f"\nBy: `{requester_id}`",
            )
        
        await bot.send_message(
            requester_id,
            "✅ Pre-ban finished"
            f"\nTarget: `{target_id if target_id is not None else target_username}`"
            f"\nSessions Used: {session_count}"
            f"\nAttempts: {attempt_count}"
            f"\nTotal Bans: {success_count}",
        )
        ban_queue.task_done()
        await asyncio.sleep(2) # Cooldown
