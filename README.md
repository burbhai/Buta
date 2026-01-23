# Buta PreBan Bot

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/burbhai/Buta)

## Requirements
- Python 3.10+
- MongoDB (recommended for production)
- Telegram API credentials

## Setup
1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Configure environment variables (see `.env.example` snippet below).
3. Run the bot:
   ```bash
   python main.py
   ```

### Required environment variables
```
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=123456:ABCDEF_your_bot_token
OWNER_IDS=123456789,987654321
MONGO_URI=mongodb://localhost:27017
DB_NAME=preban_db
```

Optional:
```
PREBAN_WORKERS=2
SESSION_CONCURRENCY=3
QUEUE_MAXSIZE=0
COMMAND_PREFIXES=/ ! .
ALLOW_DEFAULTS=0
```
Notes:
- `SESSION_CONCURRENCY=0` runs all sessions in parallel for faster pre-ban execution.

### .env example
```
API_ID=123456
API_HASH=your_api_hash
BOT_TOKEN=123456:ABCDEF_your_bot_token
OWNER_IDS=123456789
MONGO_URI=mongodb://localhost:27017
DB_NAME=preban_db
```

## Running
```bash
python main.py
```

## Self-check
Dry-run checker to validate imports, handler registration, and callback coverage:
```bash
python -m tools.selfcheck
```

## Behavior contract

### Commands
- `/start`: Show home UI. Owner gets owner panel. Users without access see payment CTA.
- `/help`: Usage and command help.
- `/ping`: Health ping + session count.
- `/preban <id|@username|t.me/...>`: Queue a pre-ban request (sudo/owner only).
- `/status`: Queue status (sudo/owner only).
- `/health`: DB + worker + queue health snapshot (owner only).
- `/verify on|off`: Toggle verification mode (owner only).
- `/verify_delay <seconds>`: Configure verify delay (owner only).
- `/set_log`: Set current chat as log group (owner only).
- `/set_session`: Set current group as session intake group (owner only, group-only).
- `/manage`: List active sessions and remove buttons (owner only).
- `/addsession <session_string>`: Add a session directly (owner only).
- `/addsudo <id|@username>`: Grant sudo access (owner only).
- `/remsudo <id|@username>`: Revoke sudo access (owner only).
- `/set <key> <value>`: Configure settings (owner only). Keys: `default_duration`, `approval_text`, `approval_durations`, `payment_rates`.
- `/cancel`: Cancel Send Love state machine.

### Inline buttons (callback_data)
All callback_data is versioned with `buta:`; legacy payloads are still accepted.

- Home/Start: `buta:start:help`, `buta:start:ping`, `buta:home`
- Payment: `buta:payment:info`, `buta:payment:how`
- Send Love: `buta:love:send`
- Owner panel: `buta:owner:panel`
- Owner actions: `buta:owner:add_sudo`, `buta:owner:remove_sudo`, `buta:owner:add_sudo:prompt`, `buta:owner:remove_sudo:prompt`
- Owner sessions: `buta:owner:manage_sessions`, `buta:session:remove:<token>`
- Owner log/session group: `buta:owner:set_log`, `buta:owner:set_session`
- Owner help shortcuts: `buta:help:verify:on`, `buta:help:verify:off`, `buta:help:manage`
- Payment approval: `buta:payment:approve:<user_id>:<hours>`
- Payment reject: `buta:payment:reject:<user_id>`

### Permissions & flows
- Owners: IDs in `OWNER_IDS`. Full access to admin actions, session ingestion, and payment approvals.
- Sudo users: Granted by owners (or via payment approval). Access to `/preban`, Send Love, and `/status`.
- Regular users: Can request payment approval by sending a screenshot in DM.

### Payment flow
1. User sends a payment screenshot in DM.
2. Bot forwards the proof to log group and posts an approval message with inline buttons.
3. Owner approves (12/24/7d durations configurable) or rejects.
4. User is notified and access expiry is extended from the current expiry (or now).

### Session ingestion
1. Owner runs `/set_session` in a group to mark it as the intake group.
2. When a session string is posted in that group, the bot validates and stores it.
3. Owners can list/remove sessions via `/manage` or owner panel.

### Send Love flow
1. User taps “Send Love”.
2. Bot enters `awaiting_target` state for 2 minutes.
3. Next text message is parsed as target and queued.
4. `/cancel` clears the state at any time.

## Troubleshooting
- If the bot exits with configuration errors, ensure required env vars are set.
- If MongoDB is unavailable, bot falls back to in-memory storage (non-persistent).

## Final checklist
- [DONE] Startup blockers fixed; modules import cleanly.
- [DONE] Handler registration is explicit via `register_*` functions.
- [DONE] Buttons/callbacks are versioned and secured.
- [DONE] Payment approval flow end-to-end.
- [DONE] Session ingestion via configured group.
- [DONE] Queue status + worker health endpoints.
- [DONE] Config validation is strict (ALLOW_DEFAULTS for local testing).
- [DONE] Logging is consistent and structured per module.
- [DONE] Self-check script provided (`python -m tools.selfcheck`).
