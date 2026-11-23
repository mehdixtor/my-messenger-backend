import os
import datetime
from zoneinfo import ZoneInfo
import random
import string
import json
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from supabase import create_client, Client

# تنظیم منطقه زمانی ایران
IRAN_TZ = ZoneInfo("Asia/Tehran")

def get_iran_time():
    return datetime.datetime.now(IRAN_TZ)

# تنظیمات Supabase از Environment Variables (Render)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL یا SUPABASE_KEY تنظیم نشده است! لطفاً در Render بررسی کنید.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

# مدیریت اتصال‌های فعال
active_connections: List[WebSocket] = []
online_map = {}  # phone -> display_name

class PhoneReq(BaseModel):
    phone: str

class VerifyReq(BaseModel):
    phone: str
    code: str

class ProfileReq(BaseModel):
    phone: str
    display_name: str
    username: str
    bio: str = ""

def gen_code():
    return "".join(random.choices(string.digits, k=6))

@app.get("/")
async def read_index():
    return FileResponse("index.html")

@app.get("/status")
async def get_status():
    return {
        "status": "Online", 
        "online_users": len(active_connections),
        "server_time": get_iran_time().isoformat()
    }

# ---------- ورود و مدیریت کد OTP (امن شده) ----------
# ---------- ورود و مدیریت کد OTP (امن شده) ----------
@app.post("/api/login")
async def api_login(req: PhoneReq):
    try:
        phone = req.phone.strip()
        res = supabase.table("users").select("*").eq("phone", phone).execute()

        if res.data:
            return {
                "is_new": False, 
                "message": "کد مربوط به این شماره را وارد کنید."
            }

        code = gen_code()
        supabase.table("users").insert({
            "phone": phone, 
            "code": code,
            "display_name": phone,
            "username": "user_" + phone[-4:]
        }).execute()
        
        return {
            "is_new": True, 
            "code": code,
            "message": "کد تایید برای شماره جدید صادر شد."
        }
    except Exception as e:
        print(f"LOGIN ERROR: {str(e)}")
        return {"error": f"خطای سرور: {str(e)}"}

@app.post("/api/verify")
async def api_verify(req: VerifyReq):
    res = supabase.table("users").select("*").eq("phone", req.phone.strip()).execute()
    if not res.data:
        return {"ok": False, "error": "کاربر پیدا نشد"}
    
    user = res.data[0]
    if str(user["code"]) != req.code.strip():
        return {"ok": False, "error": "کد اشتباه است"}
    
    return {"ok": True, "user": user}

# ---------- مدیریت پروفایل ----------
@app.post("/api/profile")
async def api_profile(req: ProfileReq):
    dup = supabase.table("users").select("phone").eq("username", req.username.strip()).execute()
    if dup.data and dup.data[0]["phone"] != req.phone:
        return {"ok": False, "error": "این نام کاربری قبلاً گرفته شده"}

    supabase.table("users").update({
        "display_name": req.display_name.strip(),
        "username": req.username.strip(),
        "bio": req.bio.strip()
    }).eq("phone", req.phone).execute()

    res = supabase.table("users").select("*").eq("phone", req.phone).execute()
    return {"ok": True, "user": res.data[0]}

# ---------- دریافت لیست کاربران ----------
@app.get("/api/users")
async def api_users(me: str = ""):
    res = supabase.table("users").select("phone,display_name,username,bio").execute()
    users = [u for u in res.data if u["phone"] != me and u.get("display_name")]
    return {"users": users}

# ---------- دریافت تاریخچه پیام‌ها ----------
@app.get("/api/history")
async def api_history(chat_id: str = "public", me: str = ""):
    q = supabase.table("messages").select("*")
    
    if chat_id == "public":
        q = q.is_("receiver_phone", "null")
    else:
        q = q.or_(f"and(sender_phone.eq.{me},receiver_phone.eq.{chat_id}),"
                  f"and(sender_phone.eq.{chat_id},receiver_phone.eq.{me})")
    
    res = q.order("created_at", desc=False).execute()
    
    out = []
    for m in res.data:
        dt_str = m.get("created_at") or ""
        time_part = ""
        if dt_str:
            try:
                utc_dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                iran_dt = utc_dt.astimezone(IRAN_TZ)
                time_part = iran_dt.strftime("%H:%M")
            except:
                time_part = dt_str[11:16]

        out.append({
            "type": "chat",
            "user": m["sender_name"],
            "sender_phone": m["sender_phone"],
            "content": m["content"],
            "timestamp": time_part,
            "chat_id": m.get("receiver_phone") or "public"
        })
    return {"messages": out}

# ---------- وب‌سوکت ----------
@app.websocket("/ws/{phone}")
async def websocket_endpoint(websocket: WebSocket, phone: str):
    await websocket.accept()
    active_connections.append(websocket)

    res = supabase.table("users").select("display_name").eq("phone", phone).execute()
    display_name = res.data[0].get("display_name") if res.data else phone
    online_map[phone] = display_name

    join_event = {
        "type": "system", 
        "content": f"⚠️ {display_name} وارد چت شد",
        "timestamp": get_iran_time().strftime("%H:%M")
    }
    for c in active_connections:
        await c.send_json(join_event)

    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            
            content = data.get("content", "")
            chat_id = data.get("chat_id", "public")
            receiver_phone = None if chat_id == "public" else chat_id

            supabase.table("messages").insert({
                "sender_phone": phone,
                "sender_name": display_name,
                "content": content,
                "receiver_phone": receiver_phone
            }).execute()

            msg_payload = {
                "type": "chat",
                "user": display_name,
                "sender_phone": phone,
                "content": content,
                "timestamp": get_iran_time().strftime("%H:%M"),
                "chat_id": chat_id
            }

            for connection in active_connections:
                await connection.send_json(msg_payload)

    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        online_map.pop(phone, None)
        
        leave_event = {
            "type": "system", 
            "content": f"❌ {display_name} از چت خارج شد",
            "timestamp": get_iran_time().strftime("%H:%M")
        }
        for c in active_connections:
            await c.send_json(leave_event)
    except Exception as e:
        print(f"WebSocket Error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
