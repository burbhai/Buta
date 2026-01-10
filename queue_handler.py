import threading, time
from typing import List, Tuple
from pyrogram import Client
import core
import db

LOCK = threading.Lock()
SUCCESS_COUNT = 0
COMPLETED_TASKS: List[Tuple[str,float]] = []

def start_queue_monitor(app: Client):
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
                # Here, simulate task (or call core.execute_preban if exists)
                elapsed = round(time.time()-start_time,2)
                with LOCK:
                    SUCCESS_COUNT +=1
                    COMPLETED_TASKS.append((username, elapsed))
                    core.ACTIVE_TASK=False
                try: app.send_message(user_id,f"✅ {username} processed | Time {elapsed}s")
                except: pass
            except: 
                with LOCK: core.ACTIVE_TASK=False
                time.sleep(2)
    threading.Thread(target=monitor, daemon=True).start()

def get_queue_status() -> str:
    with LOCK:
        q_len=len(core.QUEUE)
        active=core.ACTIVE_TASK
    return f"📊 Queue: {'Yes' if active else 'No'} | {q_len} items | Total done: {SUCCESS_COUNT}"

def get_completed_tasks_summary() -> str:
    with LOCK:
        if not COMPLETED_TASKS: return "No tasks completed yet."
        s="✅ Completed Tasks:\n"
        for i,(u,t) in enumerate(COMPLETED_TASKS,1):
            s+=f"{i}. {u} | {t}s\n"
        return s
