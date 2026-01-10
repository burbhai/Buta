import time, threading
from typing import Dict, List, Tuple
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import QUEUE_CHECK_DELAY, TASK_COOLDOWN

USER_ACCESS: Dict[int,float] = {}
WAITING_USERS = set()
QUEUE: List[Tuple[int,str]] = []
ACTIVE_TASK = False
LOG_GROUP_ID = None
SESSION_GROUP_ID = None
SESSIONS: List[dict] = []
LOCK = threading.Lock()

# ACCESS
def has_active_access(user_id:int)->bool:
    expiry = USER_ACCESS.get(user_id)
    return bool(expiry and time.time() < expiry)

def grant_access(user_id:int,hours:int):
    USER_ACCESS[user_id] = time.time() + hours*3600

def get_access_info(user_id:int)->str:
    if not has_active_access(user_id):
        return "💔 No active access."
    remaining = int(USER_ACCESS[user_id]-time.time())
    hrs, mins = remaining//3600, (remaining%3600)//60
    return f"💼 Access: {hrs}h {mins}m remaining"

# WAITING USERS
def mark_waiting(user_id:int): WAITING_USERS.add(user_id)
def is_waiting(user_id:int)->bool: return user_id in WAITING_USERS
def clear_waiting(user_id:int): WAITING_USERS.discard(user_id)

# QUEUE
def enqueue(user_id:int, username:str)->int:
    with LOCK:
        clear_waiting(user_id)
        QUEUE.append((user_id,username))
        return len(QUEUE)-1

# OWNER GROUPS
def set_log_group(chat_id:int): global LOG_GROUP_ID; LOG_GROUP_ID=chat_id
def set_session_group(chat_id:int): global SESSION_GROUP_ID; SESSION_GROUP_ID=chat_id

# SESSION OVERVIEW
def get_sessions_overview():
    if not SESSIONS: return "No sessions added", None
    rows = [[InlineKeyboardButton(f"Session {i+1} {'✅' if s['active'] else '❌'}", callback_data="noop")] for i,s in enumerate(SESSIONS)]
    return "Sessions Overview", InlineKeyboardMarkup(rows)

# SYSTEM STATUS
def get_system_status()->str:
    with LOCK: qlen=len(QUEUE)
    return f"Active task: {ACTIVE_TASK}\nQueue length: {qlen}\nLog group: {bool(LOG_GROUP_ID)}"

# WORKER
def start_worker(app:Client):
    global ACTIVE_TASK
    def loop():
        global ACTIVE_TASK
        import asyncio
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        while True:
            try:
                with LOCK:
                    if ACTIVE_TASK or not QUEUE:
                        time.sleep(QUEUE_CHECK_DELAY); continue
                    user_id, username = QUEUE.pop(0)
                    ACTIVE_TASK=True
                start = time.time(); success=0; failed=0
                async def task():
                    nonlocal success, failed
                    for s in SESSIONS:
                        if not s.get("active") or not s.get("client"): continue
                        client:Client=s["client"]
                        try:
                            async for dialog in client.get_dialogs():
                                chat = dialog.chat
                                if chat.type not in ["supergroup","channel"]: continue
                                try:
                                    me = await client.get_me()
                                    member = await client.get_chat_member(chat.id, me.id)
                                    if member.status not in ["administrator","creator"]: continue
                                except: continue
                                try:
                                    target = await client.get_users(username)
                                    await client.ban_chat_member(chat.id,target.id)
                                    success+=1
                                except: failed+=1
                        except: continue
                loop.run_until_complete(task())
                elapsed = round(time.time()-start,2)
                if LOG_GROUP_ID:
                    try: app.send_message(LOG_GROUP_ID,f"✅ {username} done. Success:{success} Failed:{failed} Time:{elapsed}s")
                    except: pass
                try: app.send_message(user_id,f"🎉 @{username} processed. Success:{success} Failed:{failed}")
                except: pass
                with LOCK: ACTIVE_TASK=False
                time.sleep(TASK_COOLDOWN)
            except:
                with LOCK: ACTIVE_TASK=False
                time.sleep(QUEUE_CHECK_DELAY)
    import threading
    t = threading.Thread(target=loop,daemon=True); t.start()
