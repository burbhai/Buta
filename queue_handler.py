import threading
import time
from typing import List, Tuple
from pyrogram import Client

import core
import db

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

    def monitor():
        global SUCCESS_COUNT

        while True:
            try:
                with LOCK:
                    if core.ACTIVE_TASK or not core.QUEUE:
                        time.sleep(core.QUEUE_CHECK_DELAY)
                        continue

                    # Get next task
                    user_id, username = core.QUEUE.pop(0)
                    core.ACTIVE_TASK = True

                # ── START TASK ──
                start_time = time.time()

                # Multi-session pre-ban logic
                try:
                    core.execute_preban(username)
                except Exception:
                    pass

                elapsed = round(time.time() - start_time, 2)

                # ── UPDATE STATS ──
                with LOCK:
                    SUCCESS_COUNT += 1
                    COMPLETED_TASKS.append((username, elapsed))
                    core.ACTIVE_TASK = False

                # ── NOTIFY USER ──
                try:
                    app.send_message(
                        chat_id=user_id,
                        text=f"✅ Task completed for {username}\n⏱ Time taken: {elapsed} sec"
                    )
                except Exception:
                    pass

                # ── LOG TO ADMIN / LOG GROUP ──
                log_group_id = db.get_setting("log_group") or core.LOG_GROUP_ID
                if log_group_id:
                    try:
                        app.send_message(
                            chat_id=log_group_id,
                            text=f"✅ {username} handled successfully\n"
                                 f"Time taken: {elapsed} sec | Total completed: {SUCCESS_COUNT}"
                        )
                    except Exception:
                        pass

            except Exception:
                # Reset active flag on any error
                with LOCK:
                    core.ACTIVE_TASK = False
                time.sleep(core.QUEUE_CHECK_DELAY)

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
    return (
        f"📊 Queue Status\n"
        f"Active Task: {'Yes' if active else 'No'}\n"
        f"Queue Length: {q_len}\n"
        f"Total Completed: {SUCCESS_COUNT}"
    )


def get_completed_tasks_summary() -> str:
    with LOCK:
        if not COMPLETED_TASKS:
            return "No tasks completed yet."
        summary = "✅ Completed Tasks:\n\n"
        for idx, (username, elapsed) in enumerate(COMPLETED_TASKS, 1):
            summary += f"{idx}. {username} | {elapsed} sec\n"
        return summary
