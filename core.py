# path: preban.py
import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List, Set

from pyrogram import Client, enums
from pyrogram.errors import PeerIdInvalid, RPCError, FloodWait

from pyrogram.raw import functions, types

from db import get_active_sessions, get_settings
from config import Config

ban_queue: "asyncio.Queue[Tuple[Any, int]]" = asyncio.Queue()
QUEUE: List[Tuple[int, str]] = []
ACTIVE_TASK = False


# -----------------------------
# User cache (access_hash store)
# -----------------------------

@dataclass(frozen=True)
class CachedUser:
    user_id: int
    access_hash: int
    username: Optional[str]
    updated_at: int


class UserCache:
    """
    Access-hash cache. For true persistence, implement in db.py:
      - async def get_user_cache(*, user_id: int|None, username: str|None) -> dict|None
      - async def upsert_user_cache(user: dict) -> None

    Fallback: in-memory cache per process.
    """
    def __init__(self, max_size: int = 50_000) -> None:
        self._by_id: Dict[int, CachedUser] = {}
        self._by_username: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._max_size = max_size

    async def get(self, *, user_id: Optional[int], username: Optional[str]) -> Optional[CachedUser]:
        # 1) Try DB hook if present
        db_get = globals().get("get_user_cache")  # if you add it in this module later
        if db_get is None:
            db_get = getattr(__import__("db"), "get_user_cache", None)

        if callable(db_get):
            try:
                row = await db_get(user_id=user_id, username=username)
                if row and row.get("user_id") and row.get("access_hash"):
                    return CachedUser(
                        user_id=int(row["user_id"]),
                        access_hash=int(row["access_hash"]),
                        username=row.get("username"),
                        updated_at=int(row.get("updated_at") or int(time.time())),
                    )
            except Exception:
                pass

        # 2) In-memory
        async with self._lock:
            if user_id is not None and user_id in self._by_id:
                return self._by_id[user_id]
            if username:
                key = username.lower().lstrip("@")
                uid = self._by_username.get(key)
                if uid and uid in self._by_id:
                    return self._by_id[uid]
        return None

    async def upsert(self, *, user_id: int, access_hash: int, username: Optional[str]) -> None:
        now = int(time.time())
        payload = {
            "user_id": int(user_id),
            "access_hash": int(access_hash),
            "username": username,
            "updated_at": now,
        }

        # 1) Try DB hook if present
        db_upsert = globals().get("upsert_user_cache")
        if db_upsert is None:
            db_upsert = getattr(__import__("db"), "upsert_user_cache", None)

        if callable(db_upsert):
            try:
                await db_upsert(payload)
            except Exception:
                pass

        # 2) In-memory
        async with self._lock:
            if len(self._by_id) >= self._max_size:
                # simple eviction: drop ~10% oldest
                items = sorted(self._by_id.values(), key=lambda x: x.updated_at)
                for old in items[: max(1, self._max_size // 10)]:
                    self._by_id.pop(old.user_id, None)
                    if old.username:
                        self._by_username.pop(old.username.lower().lstrip("@"), None)

            cu = CachedUser(
                user_id=int(user_id),
                access_hash=int(access_hash),
                username=username,
                updated_at=now,
            )
            self._by_id[cu.user_id] = cu
            if username:
                self._by_username[username.lower().lstrip("@")] = cu.user_id


USER_CACHE = UserCache()


# -----------------------------
# Helpers
# -----------------------------

def add_fallback_entity(
    fallback_entities: List[Dict[str, Any]],
    fallback_entity_ids: Set[int],
    user_id: Optional[int],
    username: Optional[str],
) -> None:
    if user_id is None or user_id in fallback_entity_ids:
        return
    fallback_entities.append({"id": user_id, "username": username})
    fallback_entity_ids.add(user_id)


async def with_floodwait(coro_factory, *, max_retries: int = 3):
    """
    Run RPC call, auto-handle FloodWait.
    Pass a zero-arg callable returning awaitable (so it can be retried cleanly).
    """
    last_exc = None
    for _ in range(max_retries):
        try:
            return await coro_factory()
        except FloodWait as e:
            await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
        except RPCError as e:
            last_exc = e
            break
    if last_exc:
        raise last_exc


async def collect_available_members(
    agent: Client,
    chat_id: int,
    fallback_entities: List[Dict[str, Any]],
    fallback_entity_ids: Set[int],
    limit: int = 10,
) -> None:
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


async def ensure_entity_with_access_hash(
    agent: Client,
    target_id: Optional[int],
    target_username: Optional[str],
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """
    Returns (user_id, access_hash, username).
    access_hash is REQUIRED for raw pre-ban when user isn't in the group.
    """
    # 0) Cache first
    cached = await USER_CACHE.get(user_id=target_id, username=target_username)
    if cached:
        return cached.user_id, cached.access_hash, cached.username or target_username

    # 1) Username -> get_users
    if target_username:
        try:
            u = await with_floodwait(lambda: agent.get_users(target_username))
            if getattr(u, "id", None) and getattr(u, "access_hash", None):
                await USER_CACHE.upsert(user_id=u.id, access_hash=u.access_hash, username=u.username)
                return u.id, u.access_hash, u.username or target_username
        except RPCError:
            pass

    # 2) ID -> get_users
    if target_id is None:
        return None, None, target_username

    try:
        u = await with_floodwait(lambda: agent.get_users(target_id))
        if getattr(u, "id", None) and getattr(u, "access_hash", None):
            await USER_CACHE.upsert(user_id=u.id, access_hash=u.access_hash, username=u.username)
            return u.id, u.access_hash, u.username or target_username
    except PeerIdInvalid:
        # try username again if present
        if target_username:
            try:
                u = await with_floodwait(lambda: agent.get_users(target_username))
                if getattr(u, "id", None) and getattr(u, "access_hash", None):
                    await USER_CACHE.upsert(user_id=u.id, access_hash=u.access_hash, username=u.username)
                    return u.id, u.access_hash, u.username or target_username
            except RPCError:
                return None, None, target_username
    except RPCError:
        return None, None, target_username

    return None, None, target_username


async def resolve_target_access_hash(
    agent: Client,
    target_id: Optional[int],
    target_username: Optional[str],
    fallback_entities: List[Dict[str, Any]],
    fallback_entity_ids: Set[int],
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    user_id, access_hash, uname = await ensure_entity_with_access_hash(agent, target_id, target_username)

    if (user_id is None or access_hash is None) and fallback_entities:
        # Warm-up: resolve a few known peers to populate session peer cache
        for fb in fallback_entities:
            try:
                await ensure_entity_with_access_hash(agent, fb.get("id"), fb.get("username"))
            except Exception:
                continue
        user_id, access_hash, uname = await ensure_entity_with_access_hash(agent, target_id, target_username)

    if user_id is not None:
        add_fallback_entity(fallback_entities, fallback_entity_ids, user_id, uname)

    return user_id, access_hash, uname


async def force_preban_raw(
    agent: Client,
    chat_id: int,
    user_id: int,
    access_hash: int,
) -> None:
    """
    Strong pre-ban (user can be not in group) using raw API.
    """
    async def _invoke():
        return await agent.invoke(
            functions.channels.EditBanned(
                channel=await agent.resolve_peer(chat_id),
                participant=types.InputPeerUser(user_id=user_id, access_hash=access_hash),
                banned_rights=types.ChatBannedRights(until_date=0, view_messages=True),
            )
        )

    await with_floodwait(_invoke, max_retries=5)


async def is_user_banned(agent: Client, chat_id: int, target_id: int) -> bool:
    try:
        async for member in agent.get_chat_members(chat_id, filter=enums.ChatMembersFilter.BANNED):
            if member.user and member.user.id == target_id:
                return True
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
        return False
    except RPCError:
        return False
    return False


def format_chat_metrics(chat_metrics: Dict[int, Dict[str, Any]]) -> str:
    if not chat_metrics:
        return ""
    lines: List[str] = []
    for chat_id, data in chat_metrics.items():
        title = data.get("title") or str(chat_id)
        lines.append(
            f"- {title} ({chat_id}): "
            f"attempts={data['attempts']} "
            f"bans={data['bans']} "
            f"verified={data['verified']}"
        )
    return "\n".join(lines)


def _can_restrict(me_member: Any) -> bool:
    if getattr(me_member, "status", None) == "creator":
        return True
    priv = getattr(me_member, "privileges", None)
    return bool(priv and getattr(priv, "can_restrict_members", False))


async def _process_one_session(
    session_row: Dict[str, Any],
    target_id: Optional[int],
    target_username: Optional[str],
    fallback_entities: List[Dict[str, Any]],
    fallback_entity_ids: Set[int],
) -> Tuple[int, int, Dict[int, Dict[str, Any]]]:
    """
    Returns: (attempts, verified_success, per_chat_metrics)
    """
    attempts = 0
    verified = 0
    chat_metrics: Dict[int, Dict[str, Any]] = {}

    agent = Client(
        f"agent_{session_row.get('id') or session_row.get('_id') or session_row.get('name') or id(session_row)}",
        session_string=session_row["string"],
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        in_memory=True,
    )

    try:
        await agent.start()

        resolved_id, access_hash, resolved_username = await resolve_target_access_hash(
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
                me_member = await with_floodwait(lambda: agent.get_chat_member(dialog.chat.id, "me"))
            except Exception:
                continue

            if not _can_restrict(me_member):
                continue

            attempts += 1
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

            if resolved_id is None or access_hash is None:
                await collect_available_members(agent, dialog.chat.id, fallback_entities, fallback_entity_ids)
                resolved_id, access_hash, resolved_username = await resolve_target_access_hash(
                    agent,
                    target_id,
                    target_username,
                    fallback_entities,
                    fallback_entity_ids,
                )
                if resolved_id is None or access_hash is None:
                    continue

            # Pre-ban RAW (works even if user not in group)
            try:
                await force_preban_raw(agent, dialog.chat.id, resolved_id, access_hash)
                chat_data["bans"] += 1
            except PeerIdInvalid:
                # Warm-up and retry once
                await collect_available_members(agent, dialog.chat.id, fallback_entities, fallback_entity_ids)
                resolved_id, access_hash, resolved_username = await resolve_target_access_hash(
                    agent,
                    resolved_id,
                    resolved_username,
                    fallback_entities,
                    fallback_entity_ids,
                )
                if resolved_id is None or access_hash is None:
                    continue
                try:
                    await force_preban_raw(agent, dialog.chat.id, resolved_id, access_hash)
                    chat_data["bans"] += 1
                except RPCError:
                    continue
            except RPCError:
                continue

            if await is_user_banned(agent, dialog.chat.id, resolved_id):
                verified += 1
                chat_data["verified"] += 1

    finally:
        try:
            await agent.stop()
        except Exception:
            pass

    return attempts, verified, chat_metrics


async def pre_ban_worker(bot, *, session_concurrency: int = 3) -> None:
    """
    Queue worker. Run multiple workers via start_preban_workers(...).
    session_concurrency limits how many sessions run in parallel per ban request.
    """
    while True:
        target_info, requester_id = await ban_queue.get()

        conf = await get_settings()
        log_group = conf.get("log_group")

        all_sessions = await get_active_sessions()

        # Metrics
        success_count = 0
        attempt_count = 0
        session_count = 0
        chat_metrics: Dict[int, Dict[str, Any]] = {}

        # Fallback warm-up pool (shared across sessions of this request)
        fallback_entities: List[Dict[str, Any]] = []
        fallback_entity_ids: Set[int] = set()

        # Normalize target
        target_id: Optional[int] = None
        target_username: Optional[str] = None
        if isinstance(target_info, dict):
            target_id = target_info.get("id")
            target_username = target_info.get("username")
        else:
            target_id = target_info

        # Run sessions in parallel (bounded)
        sem = asyncio.Semaphore(max(1, int(session_concurrency)))

        async def run_one(srow: Dict[str, Any]):
            nonlocal session_count
            async with sem:
                session_count += 1
                return await _process_one_session(
                    srow,
                    target_id,
                    target_username,
                    fallback_entities,
                    fallback_entity_ids,
                )

        tasks = [asyncio.create_task(run_one(s)) for s in all_sessions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                continue
            a, v, per_chat = r
            attempt_count += a
            success_count += v
            for cid, data in per_chat.items():
                agg = chat_metrics.setdefault(
                    cid,
                    {"title": data.get("title"), "attempts": 0, "bans": 0, "verified": 0},
                )
                agg["attempts"] += data.get("attempts", 0)
                agg["bans"] += data.get("bans", 0)
                agg["verified"] += data.get("verified", 0)

        # Notify
        metrics_block = format_chat_metrics(chat_metrics)
        if metrics_block:
            metrics_block = f"\n\n**Per-chat Metrics**\n{metrics_block}"

        target_label = target_id if target_id is not None else (target_username or "unknown")

        if log_group:
            try:
                await bot.send_message(
                    log_group,
                    "🛡 **Pre-Ban Done**"
                    f"\nTarget: `{target_label}`"
                    f"\nSessions Used: {session_count}"
                    f"\nAttempts: {attempt_count}"
                    f"\nTotal Verified Bans: {success_count}"
                    f"\nBy: `{requester_id}`"
                    f"{metrics_block}",
                )
            except Exception:
                pass

        try:
            await bot.send_message(
                requester_id,
                "✅ Pre-ban finished"
                f"\nTarget: `{target_label}`"
                f"\nSessions Used: {session_count}"
                f"\nAttempts: {attempt_count}"
                f"\nTotal Verified Bans: {success_count}"
                f"{metrics_block}",
            )
        except Exception:
            pass

        ban_queue.task_done()
        await asyncio.sleep(2)  # cooldown


def start_preban_workers(bot, *, num_workers: int = 2, session_concurrency: int = 3) -> List[asyncio.Task]:
    """
    Start multiple queue workers for speed.
    - num_workers: parallel requests
    - session_concurrency: parallel sessions per request
    """
    tasks: List[asyncio.Task] = []
    for _ in range(max(1, int(num_workers))):
        tasks.append(asyncio.create_task(pre_ban_worker(bot, session_concurrency=session_concurrency)))
    return tasks
