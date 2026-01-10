import threading
import time
from typing import List, Tuple
from pyrogram import Client

import core

# Thread lock for safety
LOCK = threading.Lock()

# Success counter
SUCCESS_COUNT = 0

# Completed tasks list (for admin stats)
COMPLETED_TASKS: List[Tuple[str, float]] = []  # (username, timestamp)


def start_queue_monitor(app: Client):
    """
    Queue monitor thread.
    Processes core.QUEUE, notifies user, updates SUCCESS_COUNT
    """
    def monitor():
        global SUCCESS_COUNT

        while True:
            try:
                with LOCK:
                    if not core.QUEUE or core.ACTIVE_TASK:
                        time.sleep(2)
                        continue

                    user_id, username = core.QUEUE.pop(0)
                    core.ACTIVE_TASK = True

                # Simulate processing (replace with real logic if needed)
                start_time = time.time()
                time.sleep(core.TASK_COOLDOWN)  # Simulated work
                elapsed = round(time.time() - start_time, 2)

                # Increment success counter
                SUCCESS_COUNT += 1
                COMPLETED_TASKS.append((username, elapsed))

                # Notify user
                try:
                    app.send_message(
                        chat_id=user_id,
                        text=f"✅ Task completed for {username}\n⏱ Time taken: {elapsed} sec"
                    )
                except Exception:
                    pass

                # Optional: log group
                if core.LOG_GROUP_ID:
                    try:
                        app.send_message(
                            chat_id=core.LOG_GROUP_ID,
                            text=f"✅ {username} handled successfully by worker\n"
                                 f"Time taken: {elapsed} sec | Total completed: {SUCCESS_COUNT}"
                        )
                    except Exception:
                        pass

                with LOCK:
                    core.ACTIVE_TASK = False

            except Exception:
                with LOCK:
                    core.ACTIVE_TASK = False
                time.sleep(2)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()


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
