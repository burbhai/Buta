import threading
import time
from typing import List, Tuple
from pyrogram import Client

import core
import db
import asyncio

# ─────────────────────────────────────────────
# THREAD LOCK & GLOBALS
# ─────────────────────────────────────────────
LOCK = threading.Lock()
SUCCESS_COUNT = 0
COMPLETED_TASKS: List[Tuple[str, float]] = []  # (username, time_taken)


# ─────────────────────────────────────────────
# QUEUE MONITOR / WORKER
# ─────────────────────────────────────────────
def start_queue_monitor(app: Client):
    """
    Background queue monitor:
    - Processes core.QUEUE
    - Sends completion notifications
    - Logs to admin/log group
    """

    async def process_task(user_id: int, username: str):
        start_time = time.time()
        try:
            # Execute multi-session pre-ban
            await core.execute_preban(username)
            elapsed = round(time.time() - start_time, 2)

            # Notify user
            try:
                await app.send_message(
                    chat_id=user_id,
                    text=f"✅ Task completed for {username}\n⏱ Time taken: {elapsed} sec"
                )
            except Exception:
                pass

            # Log to admin/log group
            log_group_id = db.get_setting("log_group") or core.LOG_GROUP_ID
            if log_group_id:
                try:
                    await app.send_message(
                        chat_id=log_group_id,
                        text=f"✅ {username} handled successfully\n"
                             f"Time taken: {elapsed} sec | Total completed: {SUCCESS_COUNT + 1}"
                    )
                except Exception:
                    pass

            return elapsed

        finally:
            with LOCK:
                core.ACTIVE_TASK = False

    def monitor():
        global SUCCESS_COUNT
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while True:
            try:
                with LOCK:
                    if core.ACTIVE_TASK or not core.QUEUE:
                        time.sleep(2)
                        continue
                    user_id, username = core.QUEUE.pop(0)
                    core.ACTIVE_TASK = True

                # Process task asynchronously in event loop
                elapsed = loop.run_until_complete(process_task(user_id, username))

                # Update stats
                with LOCK:
                    SUCCESS_COUNT += 1
                    COMPLETED_TASKS.append((username, elapsed))

            except Exception:
                with LOCK:
                    core.ACTIVE_TASK = False
                time.sleep(2)

    # ── START THREAD ──
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()


# ─────────────────────────────────────────────
# QUEUE STATUS / STATS
# ─────────────────────────────────────────────
def get_queue_status() -> str:
    with LOCK:
        q_len = len(core.QUEUE)
        active = core.ACTIVE_TASK
    return f"📊 Queue Status\nActive Task: {'Yes' if active else 'No'}\nQueue Length: {q_len}\nTotal Completed: {SUCCESS_COUNT}"


def get_completed_tasks_summary() -> str:
    with LOCK:
        if not COMPLETED_TASKS:
            return "No tasks completed yet."
        summary = "✅ Completed Tasks:\n\n"
        for idx, (username, elapsed) in enumerate(COMPLETED_TASKS, 1):
            summary += f"{idx}. {username} | {elapsed} sec\n"
        return summary
