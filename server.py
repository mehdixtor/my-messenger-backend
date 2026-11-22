import datetime
from typing import List, Dict, Any
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ... بقیه کدهای قبلی تو ...

# این خط رو اضافه کن تا فایل‌های HTML رو بشناسه
@app.get("/")
async def read_index():
    return FileResponse('index.html')


class ConnectionManager:
    """
    مدیریت اتصالات WebSocket. 
    این کلاس مسئول وصل کردن کاربران، قطع کردن آن‌ها و پخش (Broadcast) پیام‌هاست.
    """
    def __init__(self) -> None:
        # لیست تمام کاربران آنلاین
        self.active_connections: List[WebSocket] = []
        # تاریخچه پیام‌ها برای نمایش به کاربران جدید
        self.history: List[Dict[str, Any]] = []

    async def connect(self, websocket: WebSocket) -> None:
        """اتصال کاربر به سرور."""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """قطع اتصال کاربر."""
        self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """ارسال یک پیام به تمام کاربران آنلاین."""
        for connection in self.active_connections:
            await connection.send_json(message)

    def add_to_history(self, message: Dict[str, Any]) -> None:
        """ذخیره پیام در تاریخچه."""
        self.history.append(message)
        # محدود کردن تاریخچه به ۱۰۰ پیام آخر برای جلوگیری از پر شدن حافظه
        if len(self.history) > 100:
            self.history.pop(0)

    async def send_history(self, websocket: WebSocket) -> None:
        """ارسال تمام پیام‌های قبلی به کاربر جدید."""
        for msg in self.history:
            await websocket.send_json(msg)

class ChatServer:
    """
    کلاس اصلی مدیریت منطق چت.
    """
    def __init__(self) -> None:
        self.manager = ConnectionManager()

    async def handle_connection(self, websocket: WebSocket, username: str) -> None:
        await self.manager.connect(websocket)
        
        # ۱. اطلاع‌رسانی ورود کاربر
        join_event = {
            "type": "system",
            "user": "System",
            "content": f"⚠️ {username} وارد چت شد",
            "timestamp": datetime.datetime.now().strftime("%H:%M")
        }
        await self.manager.broadcast(join_event)

        # ۲. ارسال پیام‌های قبلی به کاربر جدید
        await self.manager.send_history(websocket)

        try:
            while True:
                # ۳. دریافت پیام جدید از کاربر
                data = await websocket.receive_text()
                
                chat_message = {
                    "type": "chat",
                    "user": username,
                    "content": data,
                    "timestamp": datetime.datetime.now().strftime("%H:%M")
                }
                
                # ذخیره و پخش پیام
                self.manager.add_to_history(chat_message)
                await self.manager.broadcast(chat_message)

        except WebSocketDisconnect:
            # ۴. اطلاع‌رسانی خروج کاربر
            self.manager.disconnect(websocket)
            leave_event = {
                "type": "system",
                "user": "System",
                "content": f"❌ {username} از چت خارج شد",
                "timestamp": datetime.datetime.now().strftime("%H:%M")
            }
            await self.manager.broadcast(leave_event)

# مقداردهی اولیه اپلیکیشن FastAPI
app = FastAPI(title="Professional Chat Server")
chat_service = ChatServer()

@app.get("/")
async def root() -> Dict[str, Any]:
    """بررسی سلامت سرور."""
    return {
        "status": "Online",
        "online_users": len(chat_service.manager.active_connections),
        "server_time": datetime.datetime.now().isoformat()
    }

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str) -> None:
    """نقطه ورود اصلی برای ارتباط WebSocket."""
    await chat_service.handle_connection(websocket, username)

if __name__ == "__main__":
    # اجرای سرور
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
print()