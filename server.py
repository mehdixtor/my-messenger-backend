import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client

app = FastAPI(title="Pink Rabbit Messenger", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "YOUR_SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "YOUR_SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL != "YOUR_SUPABASE_URL" else None

OWNERS = ["09904590545", "09029016512", "09193104357", "09027699911"]

active_connections: dict = {} # phone -> WebSocket
online_users: dict = {} # phone -> datetime

def get_iran_time():
    return datetime.now(ZoneInfo("Asia/Tehran"))

class PhoneReq(BaseModel):
    phone: str

class VerifyReq(BaseModel):
    phone: str
    code: str

class ProfileReq(BaseModel):
    phone: str
    display_name: str = None
    username: str = None
    bio: str = None
    avatar: str = None
    privacy: str = "everyone" # everyone / nobody

class BanReq(BaseModel):
    admin_phone: str
    target_phone: str
    minutes: int # 0 for permanent or minutes count

@app.get("/", response_class=HTMLResponse)
def get_index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h3>index.html not found</h3>"

@app.post("/api/login")
def login(req: PhoneReq):
    phone = req.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="شماره تلفن الزامی است")
    
    code = str(random.randint(100000, 999999))
    
    if supabase:
        res = supabase.table("users").select("*").eq("phone", phone).execute()
        if res.data and len(res.data) > 0:
            # Existing account: update code, DO NOT return code to client
            supabase.table("users").update({"code": code}).eq("phone", phone).execute()
            return {"status": "existing", "message": "کد تأیید برای حساب موجود ارسال شد"}
        else:
            # New account: create user and return code
            supabase.table("users").insert({
                "phone": phone,
                "code": code,
                "display_name": phone,
                "username": f"user_{phone[-4:]}",
                "bio": "سلام از پینک ربیت!",
                "avatar": "",
                "privacy": "everyone"
            }).execute()
            return {"status": "new", "code": code, "message": "حساب جدید ساخته شد"}
    return {"status": "new", "code": code}

@app.post("/api/verify")
def verify(req: VerifyReq):
    phone = req.phone.strip()
    code = req.code.strip()
    if not supabase:
        return {"success": True}
    
    res = supabase.table("users").select("*").eq("phone", phone).eq("code", code).execute()
    if res.data and len(res.data) > 0:
        user = res.data[0]
        # Check ban status
        banned_until = user.get("banned_until")
        is_banned = False
        if banned_until:
            ban_time = datetime.fromisoformat(banned_until)
            if ban_time > get_iran_time():
                is_banned = True
            else:
                supabase.table("users").update({"banned_until": None}).eq("phone", phone).execute()
        
        is_owner = phone in OWNERS
        return {
            "success": True, 
            "is_owner": is_owner, 
            "is_banned": is_banned,
            "banned_until": banned_until,
            "user": user
        }
    raise HTTPException(status_code=400, detail="کد تأیید نادرست است")

@app.get("/api/users/{phone}")
def get_users(phone: str):
    if not supabase:
        return []
    
    caller_res = supabase.table("users").select("*").eq("phone", phone).execute()
    if not caller_res.data:
        raise HTTPException(status_code=404, detail="کاربر یافت نشد")
    
    caller = caller_res.data[0]
    caller_is_owner = phone in OWNERS
    caller_privacy = caller.get("privacy", "everyone")
    
    users_res = supabase.table("users").select("*").execute()
    all_users = users_res.data or []
    
    result = []
    now = get_iran_time()
    
    for u in all_users:
        u_phone = u["phone"]
        u_is_owner = u_phone in OWNERS
        u_privacy = u.get("privacy", "everyone")
        
        # Last seen calculation & privacy rules
        last_seen_str = "آفلاین"
        if u_phone in online_users:
            last_seen_str = "آنلاین"
        else:
            db_last = u.get("last_seen")
            if db_last:
                dt = datetime.fromisoformat(db_last)
                diff_min = int((now - dt).total_seconds() / 60)
                
                # Privacy rules enforcement
                show_seen = True
                if caller_is_owner:
                    show_seen = True # Owners always see everyone's last seen
                else:
                    if caller_privacy == "nobody" or u_privacy == "nobody":
                        show_seen = False
                
                if show_seen:
                    if diff_min < 2:
                        last_seen_str = "به تازگی"
                    elif diff_min < 60:
                        last_seen_str = f"در ظرف یک ساعت ({dt.strftime('%H:%M')})"
                    elif diff_min < 1440:
                        last_seen_str = f"آخرین بازدید {dt.strftime('%H:%M')}"
                    elif diff_min < 10080:
                        last_seen_str = "در ظرف یک هفته"
                    elif diff_min < 43200:
                        last_seen_str = "در ظرف یک ماه"
                    else:
                        last_seen_str = "خیلی وقت پیش"
                else:
                    last_seen_str = "پنهان"

        result.append({
            "phone": u_phone,
            "display_name": u.get("display_name"),
            "username": u.get("username"),
            "bio": u.get("bio"),
            "avatar": u.get("avatar"),
            "privacy": u_privacy,
            "is_owner": u_is_owner,
            "banned_until": u.get("banned_until"),
            "last_seen_text": last_seen_str,
            "is_online": u_phone in online_users
        })
    return result

@app.post("/api/profile/update")
def update_profile(req: ProfileReq):
    if not supabase:
        return {"success": True}
    
    update_data = {}
    if req.display_name is not None: update_data["display_name"] = req.display_name
    if req.username is not None: update_data["username"] = req.username
    if req.bio is not None: update_data["bio"] = req.bio
    if req.avatar is not None: update_data["avatar"] = req.avatar
    if req.privacy is not None: update_data["privacy"] = req.privacy
    
    supabase.table("users").update(update_data).eq("phone", req.phone).execute()
    return {"success": True}

@app.post("/api/admin/ban")
def ban_user(req: BanReq):
    if req.admin_phone not in OWNERS:
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")
    
    if req.target_phone in OWNERS:
        raise HTTPException(status_code=400, detail="نمی‌توانید مدیران دیگر را مسدود کنید")
    
    banned_until_val = None
    if req.minutes > 0:
        banned_until_val = (get_iran_time() + timedelta(minutes=req.minutes)).isoformat()
    else:
        # Permanent ban (100 years)
        banned_until_val = (get_iran_time() + timedelta(days=36500)).isoformat()
        
    if supabase:
        supabase.table("users").update({"banned_until": banned_until_val}).eq("phone", req.target_phone).execute()
        # If user is online, send kick command via WS
        if req.target_phone in active_connections:
            # We can handle disconnect or notify via WS
            pass
            
    return {"success": True, "banned_until": banned_until_val}

@app.post("/api/admin/unban")
def unban_user(req: BanReq):
    if req.admin_phone not in OWNERS:
        raise HTTPException(status_code=403, detail="دسترسی غیرمجاز")
    if supabase:
        supabase.table("users").update({"banned_until": None}).eq("phone", req.target_phone).execute()
    return {"success": True}

@app.get("/api/messages/{phone1}/{phone2}")
def get_history(phone1: str, phone2: str):
    if not supabase:
        return []
    # Fetch public (receiver is null) or private between phone1 & phone2
    res = supabase.table("messages").select("*").or_(
        f"and(receiver_phone.is.null),and(sender_phone.eq.{phone1},receiver_phone.eq.{phone2}),and(sender_phone.eq.{phone2},receiver_phone.eq.{phone1})"
    ).order("created_at").execute()
    return res.data or []

@app.websocket("/ws/{phone}")
async def websocket_endpoint(websocket: WebSocket, phone: str):
    await websocket.accept()
    
    # Verify ban before allowing WS
    if supabase:
        res = supabase.table("users").select("banned_until").eq("phone", phone).execute()
        if res.data and res.data[0].get("banned_until"):
            dt = datetime.fromisoformat(res.data[0]["banned_until"])
            if dt > get_iran_time():
                await websocket.send_json({"type": "banned", "banned_until": res.data[0]["banned_until"]})
                await websocket.close()
                return

    active_connections[phone] = websocket
    online_users[phone] = get_iran_time()
    
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "chat":
                content = data.get("content")
                receiver = data.get("receiver_phone") # null for public
                sender_name = data.get("sender_name", phone)
                
                msg_row = {
                    "sender_phone": phone,
                    "sender_name": sender_name,
                    "content": content,
                    "receiver_phone": receiver,
                    "created_at": get_iran_time().isoformat(),
                    "is_edited": False,
                    "deleted_for_everyone": False,
                    "seen": False
                }
                
                if supabase:
                    res = supabase.table("messages").insert(msg_row).execute()
                    if res.data:
                        msg_row = res.data[0]
                
                # Broadcast to receiver and sender
                payload = {"type": "chat", "message": msg_row}
                if receiver:
                    if receiver in active_connections:
                        await active_connections[receiver].send_json(payload)
                    await websocket.send_json(payload)
                else:
                    # Public broadcast to all
                    for p, conn in active_connections.items():
                        await conn.send_json(payload)
                        
            elif msg_type == "edit":
                msg_id = data.get("id")
                new_content = data.get("content")
                if supabase and msg_id:
                    supabase.table("messages").update({"content": new_content, "is_edited": True}).eq("id", msg_id).eq("sender_phone", phone).execute()
                payload = {"type": "edit", "id": msg_id, "content": new_content}
                # Broadcast
                for p, conn in active_connections.items():
                    await conn.send_json(payload)
                    
            elif msg_type == "delete":
                msg_id = data.get("id")
                mode = data.get("mode", "everyone") # everyone or self
                if supabase and msg_id and mode == "everyone":
                    supabase.table("messages").update({"deleted_for_everyone": True, "content": "این پیام حذف شده است"}).eq("id", msg_id).eq("sender_phone", phone).execute()
                payload = {"type": "delete", "id": msg_id, "mode": mode, "sender_phone": phone}
                for p, conn in active_connections.items():
                    await conn.send_json(payload)
                    
            elif msg_type == "seen":
                other_phone = data.get("other_phone")
                if supabase and other_phone:
                    supabase.table("messages").update({"seen": True}).eq("sender_phone", other_phone).eq("receiver_phone", phone).execute()
                payload = {"type": "seen", "by_phone": phone}
                if other_phone in active_connections:
                    await active_connections[other_phone].send_json(payload)

    except WebSocketDisconnect:
        if phone in active_connections:
            del active_connections[phone]
        if phone in online_users:
            del online_users[phone]
        if supabase:
            supabase.table("users").update({"last_seen": get_iran_time().isoformat()}).eq("phone", phone).execute()