import os
import datetime
import asyncio
from typing import List, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from supabase import create_client, Client

# تنظیمات Supabase از طریق Environment Variables در Render
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# بررسی وجود کلیدها
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Error: SUPABASE_URL or SUPABASE_KEY is not set in environment variables!")

# اتصال به Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

# مدیریت کاربران آنلاین (فقط برای اطلاع‌رسانی در لحظه)
active_connections: List[WebSocket] = []

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

@app.websocket("/ws/{phone}")
async def websocket_endpoint(websocket: WebSocket, phone: str):
    await websocket.accept()
    active_connections.append(websocket)

    # ۱. دریافت اطلاعات پروفایل کاربر از Supabase
    user_data = None
    try:
        res = supabase.table("users").select("display_name").eq("phone", phone).execute()
        if res.data:
            user_data = res.data[0]
            display_name = user_data.get("display_name", phone)
        else:
            display_name = phone
    except Exception as e:
        print(f"Error fetching user: {e}")
        display_name = phone

    # ۲. اعلام ورود کاربر
    join_event = {
        "type": "system",
        "user": "System",
        "content": f"⚠️ {display_name} وارد چت شد",
        "timestamp": datetime.datetime.now().strftime("%H:%M")
    }
    for connection in active_connections:
        await connection.send_json(join_event)

    # ۳. دریافت تاریخچه پیام‌ها از Supabase (آخر ۱۰۰ پیام)
    try:
        history_res = supabase.table("messages").select("*").order("created_at", desc=True).limit(100).execute()
        # چون پیام‌ها رو از جدید به قدیم گرفتیم، برعکسشون می‌کنیم که ترتیب درست باشه
        chat_history = history_res.data[::-1]
        for msg in chat_history:
            # تبدیل فرمت دیتابیس به فرمت فرانت‌اند
            await websocket.send_json({
                "type": "chat",
                "user": msg["sender_name"],
                "content": msg["content"],
                "timestamp": datetime.datetime.now().strftime("%H:%M") # یا استخراج از msg['created_at']
            })
    except Exception as e:
        print(f"Error fetching history: {e}")

    try:
        while True:
            # دریافت پیام از کلاینت
            data = await websocket.receive_text()
            
            # ۴. ذخیره پیام در Supabase
            new_msg = {
                "sender_phone": phone,
                "sender_name": display_name,
                "content": data
            }
            
            try:
                supabase.table("messages").insert(new_msg).execute()
                
                # ۵. پخش پیام برای همه کاربران آنلاین
                chat_payload = {
                    "type": "chat",
                    "user": display_name,
                    "content": data,
                    "timestamp": datetime.datetime.now().strftime("%H:%M")
                }
                for connection in active_connections:
                    await connection.send_json(chat_payload)
            except Exception as e:
                print(f"Error saving message: {e}")

    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)

        leave_event = {
            "type": "system",
            "user": "System",
            "content": f"❌ {display_name} از چت خارج شد",
            "timestamp": datetime.datetime.now().strftime("%H:%M")
        }
        for connection in active_connections:
            await connection.send_json(leave_event)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
