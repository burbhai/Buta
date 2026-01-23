# path: preban.py
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from pyrogram import Client, enums
from pyrogram.errors import (
    ChatAdminRequired,
    FloodWait,
    PeerIdInvalid,
    RPCError,
    UserAdminInvalid,
    UserIdInvalid,
    UserNotParticipant,
    UsernameInvalid,
    UsernameNotOccupied,
)
from pyrogram.raw import functions, types

from db import get_active_sessions, get_settings
from queue_handler import mark_task_completed, mark_task_started, signal_request_started
from config import Config
from core_fixes import safe_send_message

LOGGER = logging.getLogger(__name__)

_QUEUE_MAXSIZE = max(0, int(getattr(Config, "QUEUE_MAXSIZE", 0)))
ban_queue: "asyncio.Queue[Tuple[Any, int]]" = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
WORKER_TASKS: List[asyncio.Task] = []

T = TypeVar("T")


def _normalize_chat_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _load_db_hook(name: str) -> Optional[Callable[..., Awaitable[Any]]]:
    try:
        import db  # type: ignore
    except Exception:
        return None
    fn = getattr(db, name, None)
    return fn if callable(fn) else None


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
    Access-hash cache.

    Optional db.py hooks:
      - async def get_user_cache(*, user_id: int|None, username: str|None) -> dict|None
      - async def upsert_user_cache(user: dict) -> None
    """

    def __init__(self, max_size: int = 50_000) -> None:
        self._by_id: Dict[int, CachedUser] = {}
        self._by_username: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._max_size = max_size
        self._db_get = _load_db_hook("get_user_cache")
        self._db_upsert = _load_db_hook("upsert_user_cache")

    async def get(self, *, user_id: Optional[int], username: Optional[str]) -> Optional[CachedUser]:
        if callable(self._db_get):
            try:
                row = await self._db_get(user_id=user_id, username=username)
                if row and row.get("user_id") and row.get("access_hash"):
                    return CachedUser(
                        user_id=int(row["user_id"]),
                        access_hash=int(row["access_hash"]),
                        username=row.get("username"),
                        updated_at=int(row.get("updated_at") or int(time.time())),
                    )
            except Exception:
                pass

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

        if callable(self._db_upsert):
            try:
                await self._db_upsert(payload)
            except Exception:
                pass

        async with self._lock:
            if len(self._by_id) >= self._max_size:
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

def normalize_username(username: str) -> str:
    if username is None:
        raise ValueError("username is required")
    normalized = str(username).strip().lower()
    if normalized.startswith("@"):
        normalized = normalized[1:]
    if not normalized:
        raise ValueError("username is required")
    return normalized


def _is_basic_group(chat_type: Any) -> bool:
    return chat_type in {enums.ChatType.GROUP, "group"}


def _is_supergroup(chat_type: Any) -> bool:
    return chat_type in {enums.ChatType.SUPERGROUP, "supergroup"}


def _is_channel(chat_type: Any) -> bool:
    return chat_type in {enums.ChatType.CHANNEL, "channel"}


def _needs_access_hash_for_raw(chat_type: Any) -> bool:
    return _is_supergroup(chat_type) or _is_channel(chat_type)


class PrebanError(Exception):
    pass


class FloodWaitRetriesExceeded(PrebanError):
    pass


async def with_floodwait(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
) -> T:
    last_rpc: Optional[Exception] = None
    last_fw: Optional[Exception] = None

    for _ in range(max_retries):
        try:
            return await coro_factory()
        except FloodWait as e:
            last_fw = e
            await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
            continue
        except RPCError as e:
            last_rpc = e
            break

    if last_rpc:
        raise last_rpc
    raise FloodWaitRetriesExceeded(f"FloodWait retries exceeded ({max_retries}).") from last_fw


async def resolve_public_username(app: Client, username: str) -> int:
    try:
        normalized = normalize_username(username)
        user = await app.get_users(normalized)
        user_id = int(getattr(user, "id", 0) or 0)
        if user_id <= 0:
            raise ValueError("resolved user id is invalid")
        return user_id
    except (UsernameInvalid, UsernameNotOccupied) as exc:
        raise PrebanError("Username is invalid or not occupied.") from exc
    except FloodWait as exc:
        raise PrebanError("Too many requests. Please try again later.") from exc
    except RPCError as exc:
        raise PrebanError("Failed to resolve username due to API error.") from exc
    except ValueError as exc:
        raise PrebanError(str(exc)) from exc
    except Exception as exc:
        raise PrebanError("Failed to resolve username.") from exc


async def preban_ban_now(app: Client, chat_id: int, username: str) -> Dict[str, Any]:
    normalized = None
    try:
        normalized = normalize_username(username)
        user_id = await resolve_public_username(app, normalized)
        await app.ban_chat_member(chat_id, user_id)
        return {"ok": True, "chat_id": int(chat_id), "username": normalized, "user_id": int(user_id)}
    except PrebanError as exc:
        return {"ok": False, "chat_id": int(chat_id), "username": normalized or "", "user_id": 0, "error": str(exc)}
    except (FloodWait, RPCError, ValueError) as exc:
        return {"ok": False, "chat_id": int(chat_id), "username": normalized or "", "user_id": 0, "error": str(exc)}


def parse_target_identifier(raw: str) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    raw = (raw or "").strip()
    if not raw:
        return None, None, None
    if ":" in raw:
        left, right = raw.split(":", 1)
        if left.strip().isdigit() and right.strip().isdigit():
            return int(left.strip()), int(right.strip()), None
    if raw.lstrip("@").isdigit():
        return int(raw.lstrip("@")), None, None
    return None, None, normalize_username(raw)


def build_input_peer_user(user_id: int, access_hash: int) -> types.InputPeerUser:
    return types.InputPeerUser(user_id=user_id, access_hash=access_hash)


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


def can_attempt_preban(
    chat_type: Any,
    user_id: Optional[int],
    access_hash: Optional[int],
) -> Tuple[bool, Optional[str]]:
    if user_id is None:
        return False, "missing user_id"
    if _needs_access_hash_for_raw(chat_type) and access_hash is None:
        return True, None
    return True, None


@dataclass
class BanAttemptResult:
    attempted: bool
    succeeded: bool
    verified: bool = False
    removed: bool = False
    reason: Optional[str] = None
    exception: Optional[Exception] = None


async def collect_available_members(
    agent: Client,
    chat_id: int,
    fallback_entities: List[Dict[str, Any]],
    fallback_entity_ids: Set[int],
    limit: int = 30,
) -> None:
    try:
        async for member in agent.get_chat_members(chat_id, limit=limit):
            if not member.user:
                continue
            add_fallback_entity(fallback_entities, fallback_entity_ids, member.user.id, member.user.username)
    except RPCError:
        return


async def _try_resolve_peer_user(
    agent: Client,
    target_id: Optional[int],
    target_username: Optional[str],
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    try:
        if target_username:
            key: Any = target_username if target_username.startswith("@") else f"@{target_username}"
        elif target_id is not None:
            key = target_id
        else:
            return None, None, target_username

        peer = await with_floodwait(lambda: agent.resolve_peer(key))
        if isinstance(peer, types.InputPeerUser):
            uid = int(getattr(peer, "user_id", 0) or 0)
            ah = int(getattr(peer, "access_hash", 0) or 0)
            if uid > 0 and ah != 0:
                return uid, ah, target_username
    except Exception:
        return None, None, target_username

    return None, None, target_username


async def ensure_entity_with_access_hash(
    agent: Client,
    target_id: Optional[int],
    target_username: Optional[str],
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    cached = await USER_CACHE.get(user_id=target_id, username=target_username)
    if cached:
        return cached.user_id, cached.access_hash, cached.username or target_username

    uid, ah, uname = await _try_resolve_peer_user(agent, target_id, target_username)
    if uid is not None and ah is not None:
        await USER_CACHE.upsert(user_id=uid, access_hash=ah, username=uname or target_username)
        return uid, ah, uname or target_username

    if target_username:
        try:
            u = await with_floodwait(lambda: agent.get_users(target_username))
            if getattr(u, "id", None) and getattr(u, "access_hash", None):
                await USER_CACHE.upsert(user_id=u.id, access_hash=u.access_hash, username=u.username)
                return u.id, u.access_hash, u.username or target_username
        except RPCError:
            LOGGER.exception("Failed to resolve username=%s for access_hash.", target_username)

    if target_id is None:
        return None, None, target_username

    try:
        u = await with_floodwait(lambda: agent.get_users(target_id))
        if getattr(u, "id", None) and getattr(u, "access_hash", None):
            await USER_CACHE.upsert(user_id=u.id, access_hash=u.access_hash, username=u.username)
            return u.id, u.access_hash, u.username or target_username
    except PeerIdInvalid:
        uid, ah, uname = await _try_resolve_peer_user(agent, target_id, target_username)
        if uid is not None and ah is not None:
            await USER_CACHE.upsert(user_id=uid, access_hash=ah, username=uname or target_username)
            return uid, ah, uname or target_username
        return None, None, target_username
    except RPCError:
        LOGGER.exception("Failed to resolve user_id=%s for access_hash.", target_id)
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
        for fb in fallback_entities:
            try:
                await ensure_entity_with_access_hash(agent, fb.get("id"), fb.get("username"))
            except Exception:
                continue
        user_id, access_hash, uname = await ensure_entity_with_access_hash(agent, target_id, target_username)

    if user_id is not None:
        add_fallback_entity(fallback_entities, fallback_entity_ids, user_id, uname)

    return user_id, access_hash, uname


async def resolve_target_globally(
    sessions: List[Dict[str, Any]],
    target_id: Optional[int],
    target_username: Optional[str],
) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    normalized_username = normalize_username(target_username) if target_username else None
    cached = await USER_CACHE.get(user_id=target_id, username=normalized_username)
    if cached and cached.user_id and cached.access_hash:
        return cached.user_id, cached.access_hash, cached.username or normalized_username

    for session_row in sessions:
        agent = Client(
            f"resolve_{session_row.get('id') or session_row.get('_id') or session_row.get('name') or uuid.uuid4().hex}",
            session_string=session_row["string"],
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
        )
        try:
            await agent.start()
            resolved_id, access_hash, resolved_username = await ensure_entity_with_access_hash(
                agent, target_id, normalized_username
            )
            if resolved_id is not None and access_hash is not None:
                await USER_CACHE.upsert(
                    user_id=resolved_id,
                    access_hash=access_hash,
                    username=resolved_username or normalized_username,
                )
                return resolved_id, access_hash, resolved_username or normalized_username
        except Exception:
            continue
        finally:
            try:
                await agent.stop()
            except Exception:
                pass

    return None, None, normalized_username


async def force_preban_raw(
    agent: Client,
    chat_id: int,
    user_id: int,
    access_hash: int,
    chat_type: Any,
) -> None:
    if _is_basic_group(chat_type):
        await with_floodwait(lambda: agent.ban_chat_member(chat_id, user_id=user_id), max_retries=5)
        return

    async def _invoke():
        return await agent.invoke(
            functions.channels.EditBanned(
                channel=await agent.resolve_peer(chat_id),
                participant=types.InputPeerUser(user_id=user_id, access_hash=access_hash),
                banned_rights=types.ChatBannedRights(until_date=0, view_messages=True),
            )
        )

    await with_floodwait(_invoke, max_retries=5)


async def is_user_banned_by_scan(agent: Client, chat_id: int, target_id: int, limit: int = 200) -> bool:
    try:
        async for member in agent.get_chat_members(
            chat_id,
            filter=enums.ChatMembersFilter.BANNED,
            limit=limit,
        ):
            if member.user and member.user.id == target_id:
                return True
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
    except RPCError:
        return False
    return False


async def is_user_banned(agent: Client, chat_id: int, target_id: int) -> bool:
    try:
        member = await agent.get_chat_member(chat_id, target_id)
        status = getattr(member, "status", None)
        return status in {
            enums.ChatMemberStatus.BANNED,
            enums.ChatMemberStatus.KICKED,
            "banned",
            "kicked",
        }
    except UserNotParticipant:
        return await is_user_banned_by_scan(agent, chat_id, target_id)
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
        return False
    except RPCError:
        return False


async def is_user_removed(
    agent: Client,
    chat_id: int,
    target_id: Optional[int],
    target_username: Optional[str],
) -> bool:
    if not target_username:
        return False if target_id is None else await is_user_banned_by_scan(agent, chat_id, target_id)

    query = target_username.lstrip("@")
    try:
        async for member in agent.get_chat_members(
            chat_id,
            filter=enums.ChatMembersFilter.BANNED,
            query=query,
            limit=30,
        ):
            if not member.user:
                continue
            username = getattr(member.user, "username", None)
            if username and username.lower().lstrip("@") == query.lower():
                return True
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
    except RPCError:
        return False
    return False


async def verify_removed(
    agent: Client,
    chat_id: int,
    target_username: Optional[str],
    target_id: Optional[int],
) -> bool:
    normalized_username = normalize_username(target_username) if target_username else None
    if target_id is not None:
        return await is_user_removed(agent, chat_id, target_id, normalized_username)
    if normalized_username:
        return await is_user_removed(agent, chat_id, None, normalized_username)
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
            f"skipped={data['skipped']} "
            f"verified={data['verified']} "
            f"removed={data.get('removed', 0)} "
            f"success={data.get('success', 0)}"
        )
    return "\n".join(lines)


def format_chat_failures(chat_metrics: Dict[int, Dict[str, Any]]) -> str:
    if not chat_metrics:
        return ""
    lines: List[str] = []
    for chat_id, data in chat_metrics.items():
        reason = data.get("failure_reason")
        if not reason:
            continue
        title = data.get("title") or str(chat_id)
        lines.append(f"- {title} ({chat_id}): {reason}")
    return "\n".join(lines)


def _can_restrict(me_member: Any) -> bool:
    status = getattr(me_member, "status", None)
    if status in {"creator", enums.ChatMemberStatus.OWNER}:
        return True

    priv = getattr(me_member, "privileges", None)
    if priv is not None:
        return bool(getattr(priv, "can_restrict_members", False))

    # Some pyrogram builds expose rights directly on ChatMember (esp. for groups)
    direct = getattr(me_member, "can_restrict_members", None)
    if direct is not None:
        return bool(direct)

    # Conservative fallback: if admin but no explicit flag surfaced, try anyway.
    if status in {"administrator", enums.ChatMemberStatus.ADMINISTRATOR}:
        return True

    return False


async def _safe_send(bot: Client, chat_id: int, text: str) -> None:
    sent = await safe_send_message(bot, chat_id, text)
    if not sent:
        LOGGER.warning("Failed to send message to chat_id=%s.", chat_id)


async def _safe_edit_message(bot: Client, chat_id: int, message_id: int, text: str) -> bool:
    try:
        await bot.edit_message_text(chat_id, message_id, text)
        return True
    except Exception:
        LOGGER.exception("Failed to edit message chat_id=%s message_id=%s.", chat_id, message_id)
        return False


async def preban_in_group(
    agent: Client,
    dialog: Any,
    target_peer: Optional[types.InputPeerUser],
    *,
    target_username: Optional[str],
    target_id: Optional[int],
    fallback_entities: List[Dict[str, Any]],
    fallback_entity_ids: Set[int],
    verify_enabled: bool,
    verify_delay: float,
    me_member: Optional[Any] = None,
) -> Tuple[int, int, int, int, int, Optional[Dict[str, Any]]]:
    chat_id = dialog.chat.id
    chat_title = getattr(dialog.chat, "title", None)
    chat_type = dialog.chat.type

    chat_data = {
        "id": chat_id,
        "title": chat_title,
        "attempts": 0,
        "bans": 0,
        "skipped": 0,
        "verified": 0,
        "removed": 0,
        "success": 0,
        "failure_reason": None,
    }

    if me_member is None:
        try:
            me_member = await with_floodwait(lambda: agent.get_chat_member(chat_id, "me"))
        except Exception:
            chat_data["skipped"] += 1
            chat_data["failure_reason"] = "unable to check ban rights"
            return 0, 0, 1, 0, 0, chat_data

    if not _can_restrict(me_member):
        chat_data["skipped"] += 1
        chat_data["failure_reason"] = "missing ban rights"
        return 0, 0, 1, 0, 0, chat_data

    chat_data["attempts"] += 1
    attempted = 1
    banned = 0
    verified = 0
    removed = 0
    success = 0

    resolved_id = target_peer.user_id if target_peer else target_id
    resolved_access_hash = target_peer.access_hash if target_peer else None
    resolved_username = normalize_username(target_username) if target_username else None

    async def _attempt_ban(user_id: Optional[int], access_hash: Optional[int]) -> BanAttemptResult:
        can_attempt, reason = can_attempt_preban(chat_type, user_id, access_hash)
        if not can_attempt:
            return BanAttemptResult(attempted=False, succeeded=False, reason=reason or "preban not possible")

        if user_id is None:
            return BanAttemptResult(attempted=False, succeeded=False, reason="missing user_id")

        # 1) Supergroup/Channel: raw preban if possible (works even if not participant)
        if _needs_access_hash_for_raw(chat_type) and access_hash is not None:
            try:
                await force_preban_raw(
                    agent,
                    chat_id,
                    user_id=int(user_id),
                    access_hash=int(access_hash),
                    chat_type=chat_type,
                )
                return BanAttemptResult(attempted=True, succeeded=True)
            except (ChatAdminRequired, UserAdminInvalid, UserIdInvalid, PeerIdInvalid, RPCError) as exc:
                LOGGER.warning(
                    "Raw preban failed chat_id=%s user_id=%s username=%s error=%s",
                    chat_id,
                    user_id,
                    resolved_username or target_username,
                    type(exc).__name__,
                    exc_info=True,
                )
                # Fallback to normal ban (works if participant/peer known)
                try:
                    await with_floodwait(lambda: agent.ban_chat_member(chat_id, user_id=user_id), max_retries=5)
                    return BanAttemptResult(attempted=True, succeeded=True, reason="raw_failed_normal_ok")
                except Exception as exc2:
                    return BanAttemptResult(attempted=True, succeeded=False, reason=f"{type(exc2).__name__}", exception=exc2)

        # 2) Basic group OR missing access_hash: normal ban attempt
        try:
            await with_floodwait(lambda: agent.ban_chat_member(chat_id, user_id=user_id), max_retries=5)
            return BanAttemptResult(attempted=True, succeeded=True)
        except (ChatAdminRequired, UserAdminInvalid, UserIdInvalid, PeerIdInvalid, RPCError) as exc:
            LOGGER.warning(
                "Normal ban failed chat_id=%s user_id=%s username=%s error=%s",
                chat_id,
                user_id,
                resolved_username or target_username,
                type(exc).__name__,
                exc_info=True,
            )
            if _needs_access_hash_for_raw(chat_type) and access_hash is None:
                return BanAttemptResult(attempted=True, succeeded=False, reason="missing access_hash", exception=exc)
            return BanAttemptResult(attempted=True, succeeded=False, reason=f"{type(exc).__name__}", exception=exc)

    result = BanAttemptResult(attempted=False, succeeded=False, reason="preban not attempted")

    # 1) Fast-path: already have identity (id+access_hash)
    if target_peer:
        result = await _attempt_ban(target_peer.user_id, target_peer.access_hash)
        if result.succeeded:
            banned = 1
            success = 1
            chat_data["bans"] += 1

    # 2) Resolve per-session if needed (even if user not in members list)
    if banned == 0:
        await collect_available_members(agent, chat_id, fallback_entities, fallback_entity_ids)

        local_id, local_access_hash, local_username = await resolve_target_access_hash(
            agent,
            target_id,
            resolved_username,
            fallback_entities,
            fallback_entity_ids,
        )

        if local_id is None:
            chat_data["failure_reason"] = result.reason or "unable to resolve user entity"
        else:
            result = await _attempt_ban(local_id, local_access_hash)
            if result.succeeded:
                banned = 1
                success = 1
                chat_data["bans"] += 1
                resolved_id = local_id
                resolved_access_hash = local_access_hash
                if local_username:
                    resolved_username = normalize_username(local_username)
            else:
                chat_data["failure_reason"] = result.reason

    # 3) Quick removed check
    if banned and resolved_id is not None:
        try:
            if await verify_removed(agent, chat_id, resolved_username, resolved_id):
                removed = 1
                chat_data["removed"] += 1
        except (FloodWait, RPCError) as exc:
            LOGGER.warning(
                "Verification failed chat_id=%s user_id=%s error=%s",
                chat_id,
                resolved_id,
                type(exc).__name__,
                exc_info=True,
            )

    # 4) Post-verify after delay
    if verify_enabled and banned and resolved_id is not None:
        await asyncio.sleep(max(0.0, verify_delay))
        try:
            if await is_user_banned(agent, chat_id, resolved_id):
                verified = 1
                chat_data["verified"] += 1
        except (FloodWait, RPCError) as exc:
            LOGGER.warning(
                "Post-verification failed chat_id=%s user_id=%s error=%s",
                chat_id,
                resolved_id,
                type(exc).__name__,
                exc_info=True,
            )

    if success:
        chat_data["success"] += 1
        chat_data["failure_reason"] = None

    return attempted, banned, chat_data["skipped"], verified, removed, chat_data


async def _process_one_session(
    session_row: Dict[str, Any],
    target_username: Optional[str],
    target_identity: Optional[Tuple[int, int]],
    target_id: Optional[int],
    fallback_entities: List[Dict[str, Any]],
    fallback_entity_ids: Set[int],
    verify_enabled: bool,
    verify_delay: float,
) -> Tuple[int, int, int, int, int, Dict[int, Dict[str, Any]]]:
    attempts = 0
    bans = 0
    skipped = 0
    verified = 0
    removed = 0
    chat_metrics: Dict[int, Dict[str, Any]] = {}

    agent = Client(
        f"agent_{session_row.get('id') or session_row.get('_id') or session_row.get('name') or uuid.uuid4().hex}",
        session_string=session_row["string"],
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
    )

    try:
        await agent.start()
        group_concurrency = max(1, int(getattr(Config, "GROUP_CONCURRENCY", 5)))
        group_sem = asyncio.Semaphore(group_concurrency)
        dialogs: List[Any] = []
        eligible_members: Dict[int, Any] = {}

        async for dialog in agent.get_dialogs():
            if dialog.chat.type in {enums.ChatType.GROUP, enums.ChatType.SUPERGROUP}:
                chat_id = dialog.chat.id
                chat_title = getattr(dialog.chat, "title", None)
                try:
                    me_member = await with_floodwait(lambda: agent.get_chat_member(chat_id, "me"))
                except Exception:
                    skipped += 1
                    chat_metrics.setdefault(
                        chat_id,
                        {
                            "title": chat_title,
                            "attempts": 0,
                            "bans": 0,
                            "skipped": 1,
                            "verified": 0,
                            "removed": 0,
                            "success": 0,
                            "failure_reason": "unable to check ban rights",
                        },
                    )
                    continue

                if not _can_restrict(me_member):
                    skipped += 1
                    chat_metrics.setdefault(
                        chat_id,
                        {
                            "title": chat_title,
                            "attempts": 0,
                            "bans": 0,
                            "skipped": 1,
                            "verified": 0,
                            "removed": 0,
                            "success": 0,
                            "failure_reason": "missing ban rights",
                        },
                    )
                    continue

                eligible_members[chat_id] = me_member
                dialogs.append(dialog)

        target_peer = None
        if target_identity and target_identity[0] and target_identity[1]:
            target_peer = build_input_peer_user(target_identity[0], target_identity[1])

        async def run_dialog(dialog: Any):
            async with group_sem:
                return await preban_in_group(
                    agent,
                    dialog,
                    target_peer,
                    target_username=target_username,
                    target_id=target_id,
                    fallback_entities=fallback_entities,
                    fallback_entity_ids=fallback_entity_ids,
                    verify_enabled=verify_enabled,
                    verify_delay=verify_delay,
                    me_member=eligible_members.get(dialog.chat.id),
                )

        results = await asyncio.gather(
            *[asyncio.create_task(run_dialog(dialog)) for dialog in dialogs],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception):
                continue
            a, b, s, v, r, chat_data = result
            attempts += a
            bans += b
            skipped += s
            verified += v
            removed += r
            if chat_data:
                existing = chat_metrics.setdefault(
                    chat_data["id"],
                    {
                        "title": chat_data.get("title"),
                        "attempts": 0,
                        "bans": 0,
                        "skipped": 0,
                        "verified": 0,
                        "removed": 0,
                        "success": 0,
                        "failure_reason": None,
                    },
                )
                existing["attempts"] += chat_data.get("attempts", 0)
                existing["bans"] += chat_data.get("bans", 0)
                existing["skipped"] += chat_data.get("skipped", 0)
                existing["verified"] += chat_data.get("verified", 0)
                existing["removed"] += chat_data.get("removed", 0)
                existing["success"] += chat_data.get("success", 0)
                if chat_data.get("failure_reason"):
                    existing["failure_reason"] = chat_data.get("failure_reason")

    finally:
        try:
            await agent.stop()
        except Exception:
            pass

    return attempts, bans, skipped, verified, removed, chat_metrics


async def preban_all_sessions(
    sessions: List[Dict[str, Any]],
    *,
    target_identity: Optional[Tuple[int, int]],
    target_id: Optional[int],
    target_username: Optional[str],
    verify_enabled: bool,
    verify_delay: float,
    session_concurrency: int,
) -> Tuple[int, int, int, int, int, int, Dict[int, Dict[str, Any]], Set[int], Set[int], Set[int]]:
    verified_count = 0
    removed_count = 0
    attempt_count = 0
    ban_count = 0
    skip_count = 0
    session_count = 0
    chat_metrics: Dict[int, Dict[str, Any]] = {}
    attempted_groups: Set[int] = set()
    confirmed_groups: Set[int] = set()
    removed_groups: Set[int] = set()

    fallback_entities: List[Dict[str, Any]] = []
    fallback_entity_ids: Set[int] = set()

    concurrency_limit = int(session_concurrency)
    if concurrency_limit <= 0:
        concurrency_limit = max(1, len(sessions))
    sem = asyncio.Semaphore(concurrency_limit)

    async def run_one(srow: Dict[str, Any]):
        nonlocal session_count
        async with sem:
            session_count += 1
            return await _process_one_session(
                srow,
                target_username,
                target_identity,
                target_id,
                fallback_entities,
                fallback_entity_ids,
                verify_enabled,
                verify_delay,
            )

    results = await asyncio.gather(
        *[asyncio.create_task(run_one(s)) for s in sessions],
        return_exceptions=True,
    )

    for r in results:
        if isinstance(r, Exception):
            LOGGER.exception("Pre-ban session failed.")
            continue
        a, b, s, v, removed, per_chat = r
        attempt_count += a
        ban_count += b
        skip_count += s
        verified_count += v
        removed_count += removed
        for cid, data in per_chat.items():
            if data.get("attempts", 0) > 0:
                attempted_groups.add(cid)
            if data.get("success", 0) > 0:
                confirmed_groups.add(cid)
            if data.get("removed", 0) > 0:
                removed_groups.add(cid)
            agg = chat_metrics.setdefault(
                cid,
                {
                    "title": data.get("title"),
                    "attempts": 0,
                    "bans": 0,
                    "skipped": 0,
                    "verified": 0,
                    "removed": 0,
                    "success": 0,
                    "failure_reason": None,
                },
            )
            agg["attempts"] += data.get("attempts", 0)
            agg["bans"] += data.get("bans", 0)
            agg["skipped"] += data.get("skipped", 0)
            agg["verified"] += data.get("verified", 0)
            agg["removed"] += data.get("removed", 0)
            agg["success"] += data.get("success", 0)
            if data.get("failure_reason"):
                agg["failure_reason"] = data.get("failure_reason")

    return (
        session_count,
        attempt_count,
        ban_count,
        skip_count,
        verified_count,
        removed_count,
        chat_metrics,
        attempted_groups,
        confirmed_groups,
        removed_groups,
    )


async def pre_ban_worker(bot: Client, *, session_concurrency: int = 3) -> None:
    while True:
        got_item = False
        target_label = "unknown"
        requester_id = None
        notify_chat_id = None
        notify_message_id = None
        start_time = time.time()
        success = False
        try:
            target_info, requester_id = await ban_queue.get()
            got_item = True

            if isinstance(target_info, dict):
                request_id = target_info.get("request_id")
                if request_id:
                    signal_request_started(str(request_id))
                notify_chat_id = target_info.get("notify_chat_id")
                notify_message_id = target_info.get("notify_message_id")

            target_id: Optional[int] = None
            target_username: Optional[str] = None
            target_access_hash: Optional[int] = None

            if isinstance(target_info, dict):
                target_id = target_info.get("id")
                raw_username = target_info.get("username")
                target_username = normalize_username(raw_username) if raw_username else None
                target_access_hash = target_info.get("access_hash")
            else:
                target_id = target_info

            target_label = target_id if target_id is not None else (target_username or "unknown")
            await mark_task_started(str(target_label))

            conf = await get_settings()
            log_group = _normalize_chat_id(conf.get("log_group"))
            verify_enabled = conf.get("verify_enabled", True)
            verify_delay = float(conf.get("verify_delay", 1))

            all_sessions = await get_active_sessions()
            if not all_sessions:
                msg = "❌ No active sessions configured. Ask an owner to add sessions."
                await _safe_send(bot, requester_id, msg)
                if log_group:
                    await _safe_send(bot, log_group, f"{msg}\nRequester: `{requester_id}`")
                continue

            target_identity: Optional[Tuple[int, int]] = None
            if target_id is not None and target_access_hash is not None:
                target_identity = (target_id, target_access_hash)
            else:
                resolved_id, access_hash, resolved_username = await resolve_target_globally(
                    all_sessions,
                    target_id,
                    target_username,
                )
                if resolved_id is not None and access_hash is not None:
                    target_id = resolved_id
                    target_username = normalize_username(resolved_username) if resolved_username else target_username
                    target_identity = (resolved_id, access_hash)

            (
                session_count,
                attempt_count,
                ban_count,
                skip_count,
                verified_count,
                removed_count,
                chat_metrics,
                attempted_groups,
                confirmed_groups,
                removed_groups,
            ) = await preban_all_sessions(
                all_sessions,
                target_identity=target_identity,
                target_id=target_id,
                target_username=target_username,
                verify_enabled=verify_enabled,
                verify_delay=verify_delay,
                session_concurrency=session_concurrency,
            )

            metrics_block = format_chat_metrics(chat_metrics)
            if metrics_block:
                metrics_block = f"\n\n**Per-chat Metrics**\n{metrics_block}"

            success_count = len(removed_groups)
            if success_count == 0 and attempt_count > 0:
                success_count = len(attempted_groups)
            failed_count = max(0, len(attempted_groups) - success_count)
            target_display = target_id if target_id is not None else (f"@{target_username}" if target_username else "unknown")

            failures_block = format_chat_failures(chat_metrics)
            if failures_block:
                failures_block = f"\n\n**Failures**\n{failures_block}"

            if log_group:
                await _safe_send(
                    bot,
                    log_group,
                    "🛡 **Pre-Ban Done**"
                    f"\nTarget: `{target_display}`"
                    f"\nSessions Used: {session_count}"
                    f"\nAttempts: {attempt_count}"
                    f"\nGroups Attempted: {len(attempted_groups)}"
                    f"\nBans Issued: {ban_count}"
                    f"\nSuccess: {success_count}"
                    f"\nFailed: {failed_count}"
                    f"\nSkipped: {skip_count}"
                    f"\nVerified Bans: {verified_count}"
                    f"\nRemoved(quick): {removed_count}"
                    f"\nBy: `{requester_id}`"
                    f"{metrics_block}"
                    f"{failures_block}",
                )

            await _safe_send(
                bot,
                requester_id,
                "✅ Successful love request"
                f"\nSuccess: {success_count}"
                f"\nFailed: {failed_count}",
            )
            if notify_chat_id and notify_message_id:
                await _safe_edit_message(
                    bot,
                    notify_chat_id,
                    int(notify_message_id),
                    "✅ **Successful love request**"
                    f"\nSuccess: {success_count}"
                    f"\nFailed: {failed_count}",
                )
            success = True

        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Pre-ban worker failed.")
            failure_message = "❌ Pre-ban failed due to an internal error. Please try again or contact support."
            if requester_id is not None:
                await _safe_send(bot, requester_id, failure_message)
            if notify_chat_id and notify_message_id:
                await _safe_edit_message(
                    bot,
                    notify_chat_id,
                    int(notify_message_id),
                    f"{failure_message}\nTarget: `{target_label}`",
                )
            try:
                conf = await get_settings()
                log_group = _normalize_chat_id(conf.get("log_group"))
            except Exception:
                log_group = None
            if log_group:
                await _safe_send(bot, log_group, f"{failure_message}\nTarget: `{target_label}`\nBy: `{requester_id}`")

        finally:
            if got_item:
                try:
                    await mark_task_completed(str(target_label), time.time() - start_time, success=success)
                except Exception:
                    LOGGER.exception("Failed to update queue metrics.")
                ban_queue.task_done()
                await asyncio.sleep(2)
            else:
                await asyncio.sleep(1)


def start_preban_workers(bot: Client, *, num_workers: int = 2, session_concurrency: int = 3) -> List[asyncio.Task]:
    tasks: List[asyncio.Task] = []
    for _ in range(max(1, int(num_workers))):
        tasks.append(asyncio.create_task(pre_ban_worker(bot, session_concurrency=session_concurrency)))
    register_worker_tasks(tasks)
    return tasks


def register_worker_tasks(tasks: List[asyncio.Task]) -> None:
    global WORKER_TASKS
    WORKER_TASKS = tasks


def get_worker_status() -> Dict[str, int]:
    return {"total": len(WORKER_TASKS), "alive": sum(1 for task in WORKER_TASKS if not task.done())}
