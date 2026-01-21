from __future__ import annotations

import logging
import os
import re
from typing import List, Tuple

LOGGER = logging.getLogger(__name__)


def _parse_owner_ids(raw: str) -> Tuple[List[int], List[str]]:
    tokens = [x for x in re.split(r"[,\s]+", raw.strip()) if x]
    owners: List[int] = []
    invalid: List[str] = []
    for token in tokens:
        if token.isdigit():
            owners.append(int(token))
        else:
            invalid.append(token)
    return owners, invalid


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        LOGGER.error("Invalid %s value %r. Falling back to %s.", name, raw, default)
        return default


class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    _owner_raw = os.getenv("OWNER_IDS", "")
    OWNERS, _OWNER_INVALID = _parse_owner_ids(_owner_raw)
    MONGO_URI = os.getenv("MONGO_URI", "")
    DB_NAME = os.getenv("DB_NAME", "preban_db")
    PREBAN_WORKERS = _parse_int_env("PREBAN_WORKERS", 2)
    SESSION_CONCURRENCY = _parse_int_env("SESSION_CONCURRENCY", 3)
    QUEUE_MAXSIZE = _parse_int_env("QUEUE_MAXSIZE", 0)
    _prefix_raw = os.getenv("COMMAND_PREFIXES", "/ ! .")
    COMMAND_PREFIXES = [p for p in re.split(r"[,\s]+", _prefix_raw.strip()) if p]
    if not COMMAND_PREFIXES:
        COMMAND_PREFIXES = ["/"]

    @classmethod
    def validate(cls) -> None:
        errors: List[str] = []
        if not isinstance(cls.API_ID, int) or cls.API_ID <= 0:
            errors.append("API_ID must be a valid integer greater than 0.")
        if not cls.API_HASH:
            errors.append("API_HASH is required and cannot be empty.")
        if not cls.BOT_TOKEN or not re.match(r"^\d+:[\w-]{20,}$", cls.BOT_TOKEN):
            errors.append("BOT_TOKEN is required and must look like '123456:ABC...'.")
        if cls._OWNER_INVALID:
            errors.append(f"OWNER_IDS contains invalid values: {', '.join(cls._OWNER_INVALID)}.")
        if not cls.OWNERS:
            errors.append("OWNER_IDS must contain at least one numeric Telegram user ID.")
        if not cls.MONGO_URI or not re.match(r"^mongodb(\+srv)?://", cls.MONGO_URI):
            errors.append("MONGO_URI must be a valid MongoDB connection string.")
        if cls.PREBAN_WORKERS < 1:
            errors.append("PREBAN_WORKERS must be >= 1.")
        if cls.SESSION_CONCURRENCY < 1:
            errors.append("SESSION_CONCURRENCY must be >= 1.")
        if cls.QUEUE_MAXSIZE < 0:
            errors.append("QUEUE_MAXSIZE must be >= 0.")

        if errors:
            for err in errors:
                LOGGER.error("Config validation error: %s", err)
            raise SystemExit(1)
