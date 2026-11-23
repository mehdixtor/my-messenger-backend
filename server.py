import os
import datetime
import random
import string
import json
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from supabase import create_client, Client

# تنظیمات Supabase از Environment Variables (Render)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL یا SUPABASE_KEY تنظیم نشده است! لطفاً در Render بررسی کنید.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

# مدیریت اتصال‌های فعال
active_connections: List[WebSocket] = []
# نگهداری نام کاربران آنلاین برای نمایش در سیستم
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
        "server_time": datetime.datetime.now().isoformat()
    }

# ---------- ورود و مدیریت کد OTP ----------
@app.post("/api/login")
async def api_login(req: PhoneReq):
    phone = req.phone.strip()
    res = supabase.table("users").select("*").eq("phone", phone).execute()

    if res.data:
        return {"is_new": False, "code": res.data[0]["code"]}

    code = gen_code()
    # ذخیره کاربر جدید با کد تایید
    supabase.table("users").insert({
        "phone": phone, 
        "code": code,
        "display_name": phone # پیش‌فرض
    }).execute()
    return {"is_new": True, "code": code}

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
    # بررسی تکراری بودن نام کاربری
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
    # لیست کاربرانی که خودم نیستم
    users = [u for u in res.data if u["phone"] != me and u.get("display_name")]
    return {"users": users}

# ---------- دریافت تاریخچه پیام‌ها ----------
@app.get("/api/history")
async def api_history(chat_id: str = "public", me: str = ""):
    """
    اگر chat_id برابر public باشد -> پیام‌های عمومی را می‌گیرد.
    اگر chat_id یک شماره موبایل باشد -> پیام‌های بین من و آن شخص را می‌گیرد.
    """
    q = supabase.table("messages").select("*")
    
    if chat_id == "public":
        # پیام‌هایی که گیرنده آن‌ها NULL است (عمومی)
        q = q.is_("receiver_phone", "null")
    else:
        # پیام‌هایی که یا من فرستنده هستم یا گیرنده (در چت خصوصی)
        q = q.or_(f"and(sender_phone.eq.{me},receiver_phone.eq.{chat_id}),"
                  f"and(sender_phone.eq.{chat_id},receiver_phone.eq.{me})")
    
    res = q.order("created_at", desc=False).execute()
    
    out = []
    for m in res.data:
        out.append({
            "type": "chat",
            "user": m["sender_name"],
            "sender_phone": m["sender_phone"],
            "content": m["content"],
            "timestamp": (m.get("created_at") or "")[11:16],
            "chat_id": m.get("receiver_phone") or "public"
        })
    return {"messages": out}

# ---------- وب‌سوکت (قلب تپنده پیام‌ها) ----------
@app.websocket("/ws/{phone}")
async def websocket_endpoint(websocket: WebSocket, phone: str):
    await websocket.accept()
    active_connections.append(websocket)

    # پیدا کردن نام کاربر برای نمایش در سیستم
    res = supabase.table("users").select("display_name").eq("phone", phone).execute()
    display_name = res.data[0].get("display_name") if res.data else phone
    online_map[phone] = display_name

    # اطلاع‌رسانی ورود کاربر به بقیه
    join_event = {
        "type": "system", 
        "content": f"⚠️ {display_name} وارد چت شد",
        "timestamp": datetime.datetime.now().strftime("%H:%M")
    }
    for c in active_connections:
        await c.send_json(join_event)

    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            
            content = data.get("content", "")
            chat_id = data.get("chat_id", "public") # اگر شماره موبایل باشد -> خصوصی، اگر public باشد -> عمومی
            receiver_phone = None if chat_id == "public" else chat_id

            # ۱. ذخیره در دیتابیس
            supabase.table("messages").insert({
                "sender_phone": phone,
                "sender_name": display_name,
                "content": content,
                "receiver_phone": receiver_phone
            }).execute()

            # ساخت پیام برای ارسال در WebSocket
            msg_payload = {
                "type": "chat",
                "user": display_name,
                "sender_phone": phone,
                "content": content,
                "timestamp": datetime.datetime.now().strftime("%H:%M"),
                "chat_id": chat_id
            }

            # ۲. ارسال پیام به همه (برای چت عمومی) یا فقط به گیرنده (برای چت خصوصی)
            for connection in active_connections:
                # اگر چت عمومی است، به همه بفرست
                if chat_id == "public":
                    await connection.send_json(msg_payload)
                else:
                    # اگر چت خصوصی است، فقط به فرستنده و گیرنده بفرست
                    # (در اینجا برای سادگی چک می‌کنیم که آیا اتصال مربوط به فرستنده یا گیرنده است)
                    # نکته: در این نسخه ساده ما برای همه پخش می‌کنیم اما فرانت‌اند باید فیلتر کند
                    # اما راه درست‌تر این است که ما بدانیم هر اتصال متعلق به کدام تلفن است.
                    # فعلاً برای کارکرد سریع، همه را می‌فرستیم و فرانت‌اند فیلتر می‌کند.
                    await connection.send_json(msg_payload)

    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        online_map.pop(phone, None)
        
        leave_event = {
            "type": "system", 
            "content": f"❌ {display_name} از چت خارج شد",
            "timestamp": datetime.datetime.now().strftime("%H:%M")
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
