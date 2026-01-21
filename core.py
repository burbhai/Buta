import asyncio
from pyrogram import Client, enums
from pyrogram.errors import PeerIdInvalid, RPCError
from db import get_active_sessions, get_settings
from config import Config

ban_queue = asyncio.Queue()

async def gather_fallback_members(agent, chat_id, limit=30, retries=2):
    members = []
    attempt = 0
    while attempt <= retries:
        try:
            async for member in agent.get_chat_members(chat_id):
                if member.user:
                    members.append(member.user.id)
                if len(members) >= limit:
                    break
            return members
        except PeerIdInvalid:
            attempt += 1
            if attempt > retries:
                return members
        except RPCError:
            return members
    return members

async def inject_fallback_entities(agent, member_ids):
    if not member_ids:
        return
    try:
        await agent.get_users(member_ids)
    except RPCError:
        return

async def resolve_with_fallback(agent, chat_id, target_id, target_username, fallback_cache):
    fallback_members = fallback_cache.get(chat_id)
    if fallback_members is None:
        fallback_members = await gather_fallback_members(agent, chat_id)
        fallback_cache[chat_id] = fallback_members
    await inject_fallback_entities(agent, fallback_members)
    return await ensure_entity(agent, target_id, target_username)

async def ensure_entity(agent, target_id, target_username):
    resolved_id = target_id
    resolved_username = target_username
    if resolved_username:
        try:
            user = await agent.get_users(resolved_username)
            return user.id, user.username or resolved_username
        except RPCError:
            pass
    if resolved_id is None:
        return resolved_id, resolved_username
    try:
        user = await agent.get_users(resolved_id)
        return user.id, user.username or resolved_username
    except PeerIdInvalid:
        if resolved_username:
            user = await agent.get_users(resolved_username)
            return user.id, user.username or resolved_username
    except RPCError:
        pass
    return resolved_id, resolved_username

async def is_user_banned(agent, chat_id, target_id):
    try:
        async for member in agent.get_chat_members(
            chat_id,
            filter=enums.ChatMembersFilter.BANNED,
        ):
            if member.user and member.user.id == target_id:
                return True
    except RPCError:
        return False
    return False

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
                fallback_cache = {}
                resolved_id, resolved_username = await ensure_entity(
                    agent,
                    target_id,
                    target_username,
                )
                if resolved_id is None:
                    await agent.stop()
                    continue

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
                        await agent.get_chat_member(dialog.chat.id, resolved_id)
                    except PeerIdInvalid:
                        resolved_id, resolved_username = await resolve_with_fallback(
                            agent,
                            dialog.chat.id,
                            resolved_id,
                            resolved_username,
                            fallback_cache,
                        )
                    except RPCError:
                        pass
                    try:
                        # Ban target (works even if user is not in group)
                        await agent.ban_chat_member(dialog.chat.id, resolved_id)
                    except PeerIdInvalid:
                        resolved_id, resolved_username = await resolve_with_fallback(
                            agent,
                            dialog.chat.id,
                            resolved_id,
                            resolved_username,
                            fallback_cache,
                        )
                        try:
                            await agent.ban_chat_member(dialog.chat.id, resolved_id)
                        except RPCError:
                            continue
                    except RPCError:
                        continue
                    if await is_user_banned(agent, dialog.chat.id, resolved_id):
                        success_count += 1
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
