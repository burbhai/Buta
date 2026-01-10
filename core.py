import time, threading, asyncio
from typing import Dict, List, Tuple
from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import QUEUE_CHECK_DELAY, TASK_COOLDOWN

USER_ACCESS: Dict[int,float]={}
WAITING_USERS = set()
QUEUE:List[Tuple[int,str]]=[]
ACTIVE_TASK=False
LOG_GROUP_ID=None
SESSION_GROUP_ID=None
SESSIONS:List[dict]=[]
LOCK = threading.Lock()

def has_active_access(user_id:int)->bool:
    expiry = USER_ACCESS.get(user_id)
    return bool(expiry and time.time()<expiry)

def grant_access(user_id:int,hours:int):
    USER_ACCESS[user_id]=time.time()+hours*3600

def mark_waiting_for_username(user_id:int):
    WAITING_USERS.add(user_id)
def clear_waiting(user_id:int):
    WAITING_USERS.discard(user_id)
def is_waiting_for_username(user_id:int)->bool:
    return user_id in WAITING_USERS

def enqueue_request(user_id:int,username:str)->int:
    with LOCK:
        clear_waiting(user_id)
        QUEUE.append((user_id,username))
        return len(QUEUE)-1

def set_log_group(chat_id:int):
    global LOG_GROUP_ID
    LOG_GROUP_ID=chat_id

def set_session_group(chat_id:int):
    global SESSION_GROUP_ID
    SESSION_GROUP_ID=chat_id

def get_sessions_overview():
    if not SESSIONS: return "No sessions yet", None
    rows=[]
    for i,s in enumerate(SESSIONS):
        status="✅ ON" if s.get("active") else "❌ OFF"
        rows.append([InlineKeyboardButton(f"Session {i+1} {status}",callback_data="noop")])
    return "Sessions", InlineKeyboardMarkup(rows)

def get_system_status():
    with LOCK:
        q_len=len(QUEUE)
    return f"Active: {'Yes' if ACTIVE_TASK else 'No'}\nQueue: {q_len}\nLog: {'Yes' if LOG_GROUP_ID else 'No'}\nSessionGrp: {'Yes' if SESSION_GROUP_ID else 'No'}"

def start_worker(app:Client):
    global ACTIVE_TASK
    def worker_loop():
        global ACTIVE_TASK
        loop=asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while True:
            try:
                with LOCK:
                    if ACTIVE_TASK or not QUEUE:
                        time.sleep(QUEUE_CHECK_DELAY)
                        continue
                    user_id,username=QUEUE.pop(0)
                    ACTIVE_TASK=True
                start=time.time()
                success=0
                fail=0
                async def process_user():
                    nonlocal success,fail
                    for sess in SESSIONS:
                        if not sess.get("active") or not sess.get("client"): continue
                        client:Client=sess["client"]
                        try:
                            async for dialog in client.get_dialogs():
                                chat=dialog.chat
                                if chat.type not in ["supergroup","channel"]: continue
                                try:
                                    me=await client.get_me()
                                    member=await client.get_chat_member(chat.id,me.id)
                                    if member.status not in ["administrator","creator"]: continue
                                except: continue
                                try:
                                    target=await client.get_users(username)
                                    await client.ban_chat_member(chat.id,target.id)
                                    success+=1
                                except: fail+=1
                        except: continue
                loop.run_until_complete(process_user())
                elapsed=round(time.time()-start,2)
                if LOG_GROUP_ID:
                    try: app.send_message(LOG_GROUP_ID,f"{username} done\nSuccess:{success} Fail:{fail} Time:{elapsed}s")
                    except: pass
                try: app.send_message(user_id,f"{username} processed\nSuccess:{success} Fail:{fail}")
                except: pass
                with LOCK: ACTIVE_TASK=False
                time.sleep(TASK_COOLDOWN)
            except:
                with LOCK: ACTIVE_TASK=False
                time.sleep(QUEUE_CHECK_DELAY)
    threading.Thread(target=worker_loop,daemon=True).start()
