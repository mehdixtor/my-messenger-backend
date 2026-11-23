import os
import datetime
import random
import string
import json
from typing import List, Optional
from zoneinfo import ZoneInfo
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from supabase import create_client, Client

# --- تنظیمات اولیه ---
IRAN_TZ = ZoneInfo("Asia/Tehran")

def get_iran_time():
    return datetime.datetime.now(IRAN_TZ)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("خطا: متغیرهای محیطی Supabase یافت نشدند!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()

# مدیریت اتصالات فعال
active_connections: List[WebSocket] = []
# phone -> {display_name, is_online}
online_users_info = {}

# --- مدل‌های داده‌ای (Schemas) ---

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
    profile_pic_url: Optional[str] = None
    privacy_setting: str = "everyone"  # everyone, nobody

class MessageAction(BaseModel):
    phone: str
    message_id: str
    action: str  # "edit" or "delete"
    new_content: Optional[str] = None

class BanReq(BaseModel):
    admin_phone: str
    target_phone: str
    until_timestamp: str # ISO format

# --- توابع کمکی (Helpers) ---

def gen_code():
    return "".join(random.choices(string.digits, k=6))

async def delete_physical_file(file_url: str):
    """حذف فایل فیزیکی از Supabase Storage"""
    try:
        if not file_url or "supabase" not in file_url:
            return
        path = file_url.split("storage/v1/object/public/avatars/")[1]
        supabase.storage.from_("avatars").remove([path])
    except Exception as e:
        print(f"Cleanup Error: {e}")

# --- API Endpoints ---

@app.get("/")
async def read_index():
    return FileResponse("index.html")

@app.get("/api/status")
async def get_status():
    return {
        "status": "Online", 
        "online_users": len(active_connections),
        "server_time": get_iran_time().isoformat()
    }

# --- ورود و احراز هویت ---

@app.post("/api/login")
async def api_login(req: PhoneReq):
    phone = req.phone.strip()
    try:
        res = supabase.table("users").select("*").eq("phone", phone).execute()
        
        if res.data:
            user = res.data[0]
            # چک کردن بن بودن
            if user.get("ban_until"):
                ban_end = datetime.datetime.fromisoformat(user["ban_until"].replace("Z", "+00:00"))
                if datetime.datetime.now(IRAN_TZ) < ban_end:
                    return {"is_new": False, "is_banned": True, "until": user["ban_until"]}
            
            # کاربر قدیمی است: طبق خواسته شما کد نمایش داده نمی‌شود
            return {"is_new": False}
        
        # کاربر جدید است: تولید کد و ذخیره
        code = gen_code()
        supabase.table("users").upsert({
            "phone": phone, 
            "code": code,
            "display_name": phone,
            "username": f"user_{phone.replace('+', '')[-4:]}"
        }, on_conflict="phone").execute()
        
        return {"is_new": True, "code": code}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/verify")
async def api_verify(req: VerifyReq):
    try:
        phone = req.phone.strip()
        input_code = req.code.strip()
        
        res = supabase.table("users").select("*").eq("phone", phone).execute()
        if not res.data:
            return {"ok": False, "error": "کاربر یافت نشد"}
        
        user = res.data[0]
        # تبدیل هر دو به استرینگ و حذف فاصله‌ها برای مقایسه دقیق
        db_code = str(user.get("code", "")).strip()
        
        if input_code == db_code or not db_code:
            # پاک کردن کد پس از ورود موفق
            supabase.table("users").update({"code": None}).eq("phone", phone).execute()
            return {"ok": True, "user": user}
        
        return {"ok": False, "error": "کد اشتباه است"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- مدیریت پروفایل و عکس ---

@app.post("/api/profile/update")
async def api_profile_update(req: ProfileReq):
    try:
        old_res = supabase.table("users").select("profile_pic_url").eq("phone", req.phone).execute()
        old_url = old_res.data[0].get("profile_pic_url") if old_res.data else None

        if req.profile_pic_url and req.profile_pic_url != old_url:
            if old_url:
                await delete_physical_file(old_url)

        supabase.table("users").update({
            "display_name": req.display_name.strip(),
            "username": req.username.strip(),
            "bio": req.bio.strip(),
            "privacy_setting": req.privacy_setting,
            "profile_pic_url": req.profile_pic_url
        }).eq("phone", req.phone).execute()

        new_res = supabase.table("users").select("*").eq("phone", req.phone).execute()
        return {"ok": True, "user": new_res.data[0]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- مدیریت پیام‌ها (Edit / Delete / Ban) ---

@app.post("/api/messages/action")
async def api_message_action(req: MessageAction):
    try:
        msg_res = supabase.table("messages").select("*").eq("id", req.message_id).execute()
        if not msg_res.data: return {"ok": False, "error": "پیام یافت نشد"}
        
        if req.action == "edit":
            supabase.table("messages").update({"content": req.new_content, "is_edited": True}).eq("id", req.message_id).execute()
        elif req.action == "delete":
            supabase.table("messages").update({"content": "این پیام حذف شد", "is_deleted": True}).eq("id", req.message_id).execute()
        
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/admin/ban")
async def api_ban_user(req: BanReq):
    try:
        admin_res = supabase.table("users").select("is_owner").eq("phone", req.admin_phone).execute()
        if not admin_res.data or not admin_res.data[0]["is_owner"]:
            raise HTTPException(status_code=403, detail="عدم دسترسی ادمین")

        supabase.table("users").update({"ban_until": req.until_timestamp}).eq("phone", req.target_phone).execute()
        return {"ok": True, "message": "کاربر با موفقیت بن شد."}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- دریافت داده‌ها ---

@app.get("/api/users")
async def api_users_list(me: str = ""):
    try:
        res = supabase.table("users").select("phone,display_name,username,bio,profile_pic_url,is_owner").execute()
        users = [u for u in res.data if u["phone"] != me]
        return {"users": users}
    except Exception as e:
        return {"users": [], "error": str(e)}

@app.get("/api/history")
async def api_history_list(chat_id: str = "public", me: str = ""):
    try:
        res = supabase.table("messages").select("*, sender_name").execute()
        
        # فیلتر کردن پیام‌ها بر اساس چت (Public یا Private)
        filtered_messages = []
        for m in res.data:
            is_public = (chat_id == "public" and m.get("receiver_phone") is None)
            is_private = (chat_id != "public" and (
                (m["sender_phone"] == me and m["receiver_phone"] == chat_id) or
                (m["sender_phone"] == chat_id and m["receiver_phone"] == me)
            ))
            
            if is_public or is_private:
                content = "<i class='deleted-msg'>این پیام حذف شد</i>" if m.get("is_deleted") else m["content"]
                
                # زمان‌بندی ساده
                dt_str = m.get("created_at")
                time_display = dt_str[:16].replace("T", " ") if dt_str else ""
                
                filtered_messages.append({
                    "id": m["id"],
                    "type": "chat",
                    "user": m["sender_name"],
                    "sender_phone": m["sender_phone"],
                    "content": content,
                    "timestamp": time_display,
                    "chat_id": m.get("receiver_phone") or "public",
                    "is_edited": m.get("is_edited", False)
                })

        # مرتب‌سازی بر اساس زمان
        filtered_messages.sort(key=lambda x: x["timestamp"])
        return {"messages": filtered_messages}
    except Exception as e:
        return {"messages": [], "error": str(e)}

# --- WebSocket Real-time ---

@app.websocket("/ws/{phone}")
async def websocket_endpoint(websocket: WebSocket, phone: str):
    await websocket.accept()
    active_connections.append(websocket)

    user_res = supabase.table("users").select("display_name").eq("phone", phone).execute()
    display_name = user_res.data[0].get("display_name") if user_res.data else phone
    online_users_info[phone] = display_name

    await broadcast({"type": "system", "content": f"⚠️ {display_name} وارد شد", "timestamp": get_iran_time().strftime("%H:%M")})

    try:
        while True:
            raw_data = await websocket.receive_text()
            data = json.loads(raw_data)
            
            # چک کردن بن بودن
            user_check = supabase.table("users").select("ban_until").eq("phone", phone).execute()
            if user_check.data and user_check.data[0].get("ban_until"):
                ban_end = datetime.datetime.fromisoformat(user_check.data[0]["ban_until"].replace("Z", "+00:00"))
                if datetime.datetime.now(IRAN_TZ) < ban_end:
                    await websocket.send_json({"type": "error", "content": "شما در حالت بن هستید!"})
                    continue

            chat_id = data.get("chat_id", "public")
            receiver_phone = None if chat_id == "public" else chat_id
            
            new_msg = supabase.table("messages").insert({
                "sender_phone": phone,
                "sender_name": display_name,
                "content": data["content"],
                "receiver_phone": receiver_phone
            }).execute()

            msg_payload = {
                "id": new_msg.data[0]["id"],
                "type": "chat",
                "user": display_name,
                "sender_phone": phone,
                "content": data["content"],
                "timestamp": get_iran_time().strftime("%H:%M"),
                "chat_id": chat_id
            }
            await broadcast(msg_payload)

    except WebSocketDisconnect:
        if websocket in active_connections: active_connections.remove(websocket)
        online_users_info.pop(phone, None)
        await broadcast({"type": "system", "content": f"❌ {display_name} خارج شد", "timestamp": get_iran_time().strftime("%H:%M")})
    except Exception as e:
        print(f"WS Error: {e}")

async def broadcast(message: dict):
    for connection in active_connections:
        try:
            await connection.send_json(message)
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
