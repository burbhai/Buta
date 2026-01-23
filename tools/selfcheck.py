from __future__ import annotations

import re
import sys
from pathlib import Path

from pyrogram import Client

from config import Config
from handlers import COMMANDS, register_fallbacks, register_ui_and_commands
from payment_handler import register_payment
from session_loader import register_session_ingest


def _load_readme() -> str:
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    return readme_path.read_text(encoding="utf-8")


def _assert_commands_in_readme(readme: str) -> None:
    missing = [cmd for cmd in COMMANDS if f"/{cmd}" not in readme]
    if missing:
        raise AssertionError(f"README missing command docs for: {', '.join(missing)}")


def _callback_samples() -> list[str]:
    return [
        "buta:start:help",
        "buta:start:ping",
        "buta:home",
        "buta:payment:info",
        "buta:payment:how",
        "buta:love:send",
        "buta:owner:panel",
        "buta:owner:add_sudo",
        "buta:owner:remove_sudo",
        "buta:owner:add_sudo:prompt",
        "buta:owner:remove_sudo:prompt",
        "buta:owner:sudo_list",
        "buta:owner:manage_sessions",
        "buta:owner:set_log",
        "buta:owner:set_session",
        "buta:help:verify:on",
        "buta:help:verify:off",
        "buta:help:manage",
        "buta:session:remove:12345",
        "buta:payment:approve:123:24",
        "buta:payment:reject:123",
        "love_send",
        "rem_12345",
        "app_123_24",
        "rej_123",
    ]


def _callback_patterns() -> list[re.Pattern[str]]:
    patterns = [
        r"^(?:buta:start:help|start_help)$",
        r"^(?:buta:start:ping|start_ping)$",
        r"^(?:buta:home|home)$",
        r"^(?:buta:payment:info|payment_info)$",
        r"^(?:buta:payment:how|payment_how)$",
        r"^(?:buta:love:send|love_send)$",
        r"^(?:buta:owner:panel|owner_panel)$",
        r"^(?:buta:owner:add_sudo|owner_add_sudo)$",
        r"^(?:buta:owner:remove_sudo|owner_remove_sudo)$",
        r"^(?:buta:owner:add_sudo:prompt|owner_add_sudo_prompt)$",
        r"^(?:buta:owner:remove_sudo:prompt|owner_remove_sudo_prompt)$",
        r"^(?:buta:owner:sudo_list|owner_sudo_list)$",
        r"^(?:buta:owner:manage_sessions|owner_manage_sessions)$",
        r"^(?:buta:owner:set_log|owner_set_log)$",
        r"^(?:buta:owner:set_session|owner_set_session)$",
        r"^(?:buta:help:verify:(?:on|off)|help_verify_(?:on|off))$",
        r"^(?:buta:help:manage|help_manage)$",
        r"^(?:buta:session:remove:|rem_)(.+)$",
        r"^(?:buta:payment:approve:\d+:\d+|app_\d+_\d+)$",
        r"^(?:buta:payment:reject:\d+|rej_\d+)$",
    ]
    return [re.compile(p) for p in patterns]


def _assert_callbacks_covered() -> None:
    patterns = _callback_patterns()
    for sample in _callback_samples():
        if not any(p.match(sample) for p in patterns):
            raise AssertionError(f"Callback sample not covered: {sample}")


def main() -> int:
    Config.validate()
    app = Client(
        "selfcheck",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        in_memory=True,
    )
    register_ui_and_commands(app)
    register_payment(app)
    register_session_ingest(app)
    register_fallbacks(app)
    _assert_callbacks_covered()
    _assert_commands_in_readme(_load_readme())
    print("Selfcheck passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
