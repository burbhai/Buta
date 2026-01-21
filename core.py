import asyncio
from pyrogram import Client, enums
from pyrogram.errors import PeerIdInvalid, RPCError
from db import get_active_sessions, get_settings
from config import Config

ban_queue = asyncio.Queue()

def add_fallback_entity(fallback_entities, fallback_entity_ids, user_id, username):
    if user_id is None or user_id in fallback_entity_ids:
        return
    fallback_entities.append({"id": user_id, "username": username})
    fallback_entity_ids.add(user_id)

async def collect_available_members(
    agent,
    chat_id,
    fallback_entities,
    fallback_entity_ids,
    limit=10,
):
    try:
        async for member in agent.get_chat_members(chat_id, limit=limit):
            if not member.user:
                continue
            add_fallback_entity(
                fallback_entities,
                fallback_entity_ids,
                member.user.id,
                member.user.username,
            )
    except RPCError:
        return

async def resolve_target(
    agent,
    target_id,
    target_username,
    fallback_entities,
    fallback_entity_ids,
):
    resolved_id, resolved_username = await ensure_entity(agent, target_id, target_username)
    if resolved_id is None and fallback_entities:
        for fallback in fallback_entities:
            await ensure_entity(
                agent,
                fallback.get("id"),
                fallback.get("username"),
            )
        resolved_id, resolved_username = await ensure_entity(
            agent,
            target_id,
            target_username,
        )
    if resolved_id is not None:
        add_fallback_entity(
            fallback_entities,
            fallback_entity_ids,
            resolved_id,
            resolved_username,
        )
    return resolved_id, resolved_username

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
        return None, resolved_username
    try:
        user = await agent.get_users(resolved_id)
        return user.id, user.username or resolved_username
    except PeerIdInvalid:
        if resolved_username:
            try:
                user = await agent.get_users(resolved_username)
                return user.id, user.username or resolved_username
            except RPCError:
                return None, resolved_username
    except RPCError:
        return None, resolved_username
    return None, resolved_username

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

def format_chat_metrics(chat_metrics):
    if not chat_metrics:
        return ""
    lines = []
    for chat_id, data in chat_metrics.items():
        title = data.get("title") or str(chat_id)
        lines.append(
            f"- {title} ({chat_id}): "
            f"attempts={data['attempts']} "
            f"bans={data['bans']} "
            f"verified={data['verified']}"
        )
    return "\n".join(lines)

async def pre_ban_worker(bot):
    while True:
        target_info, requester_id = await ban_queue.get()
        conf = await get_settings()
        log_group = conf.get("log_group")

        all_sessions = await get_active_sessions()
        success_count = 0
        attempt_count = 0
        session_count = 0
        chat_metrics = {}
        fallback_entities = []
        fallback_entity_ids = set()
        
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
                resolved_id, resolved_username = await resolve_target(
                    agent,
                    target_id,
                    target_username,
                    fallback_entities,
                    fallback_entity_ids,
                )

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
                    chat_data = chat_metrics.setdefault(
                        dialog.chat.id,
                        {
                            "title": getattr(dialog.chat, "title", None),
                            "attempts": 0,
                            "bans": 0,
                            "verified": 0,
                        },
                    )
                    chat_data["attempts"] += 1
                    if resolved_id is None:
                        await collect_available_members(
                            agent,
                            dialog.chat.id,
                            fallback_entities,
                            fallback_entity_ids,
                        )
                        resolved_id, resolved_username = await resolve_target(
                            agent,
                            target_id,
                            target_username,
                            fallback_entities,
                            fallback_entity_ids,
                        )
                        if resolved_id is None:
                            continue
                    try:
                        await agent.get_chat_member(dialog.chat.id, resolved_id)
                    except PeerIdInvalid:
                        await collect_available_members(
                            agent,
                            dialog.chat.id,
                            fallback_entities,
                            fallback_entity_ids,
                        )
                        resolved_id, resolved_username = await resolve_target(
                            agent,
                            resolved_id,
                            resolved_username,
                            fallback_entities,
                            fallback_entity_ids,
                        )
                        if resolved_id is None:
                            continue
                    except RPCError:
                        pass
                    try:
                        # Ban target (works even if user is not in group)
                        await agent.ban_chat_member(dialog.chat.id, resolved_id)
                    except PeerIdInvalid:
                        await collect_available_members(
                            agent,
                            dialog.chat.id,
                            fallback_entities,
                            fallback_entity_ids,
                        )
                        resolved_id, resolved_username = await resolve_target(
                            agent,
                            resolved_id,
                            resolved_username,
                            fallback_entities,
                            fallback_entity_ids,
                        )
                        if resolved_id is None:
                            continue
                        try:
                            await agent.ban_chat_member(dialog.chat.id, resolved_id)
                        except RPCError:
                            continue
                    except RPCError:
                        continue
                    chat_data["bans"] += 1
                    if await is_user_banned(agent, dialog.chat.id, resolved_id):
                        success_count += 1
                        chat_data["verified"] += 1
                await agent.stop()
            except Exception:
                continue

        if log_group:
            metrics_block = format_chat_metrics(chat_metrics)
            if metrics_block:
                metrics_block = f"\n\n**Per-chat Metrics**\n{metrics_block}"
            await bot.send_message(
                log_group,
                "🛡 **Pre-Ban Done**"
                f"\nTarget: `{target_id if target_id is not None else target_username}`"
                f"\nSessions Used: {session_count}"
                f"\nAttempts: {attempt_count}"
                f"\nTotal Verified Bans: {success_count}"
                f"\nBy: `{requester_id}`"
                f"{metrics_block}",
            )

        metrics_block = format_chat_metrics(chat_metrics)
        if metrics_block:
            metrics_block = f"\n\n**Per-chat Metrics**\n{metrics_block}"
        await bot.send_message(
            requester_id,
            "✅ Pre-ban finished"
            f"\nTarget: `{target_id if target_id is not None else target_username}`"
            f"\nSessions Used: {session_count}"
            f"\nAttempts: {attempt_count}"
            f"\nTotal Verified Bans: {success_count}"
            f"{metrics_block}",
        )
        ban_queue.task_done()
        await asyncio.sleep(2) # Cooldown
