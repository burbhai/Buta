import threading
import time
from typing import List, Tuple
import core

LOCK = threading.Lock()
SUCCESS_COUNT = 0
COMPLETED_TASKS: List[Tuple[str, float]] = []

def start_queue_monitor(app):
    def monitor():
        global SUCCESS_COUNT
        while True:
            try:
                with LOCK:
                    if core.ACTIVE_TASK or not core.QUEUE:
                        time.sleep(2)
                        continue
                    user_id, username = core.QUEUE.pop(0)
                    core.ACTIVE_TASK = True

                start_time = time.time()
                # Simulate processing (or call core.execute_preban)
                time.sleep(1)
                elapsed = round(time.time() - start_time, 2)

                with LOCK:
                    SUCCESS_COUNT += 1
                    COMPLETED_TASKS.append((username, elapsed))
                    core.ACTIVE_TASK = False

                try:
                    app.send_message(user_id, f"✅ Task completed for {username} | {elapsed}s")
                except Exception:
                    pass
            except Exception:
                with LOCK:
                    core.ACTIVE_TASK = False
                time.sleep(2)
    threading.Thread(target=monitor, daemon=True).start()
