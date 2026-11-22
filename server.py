import datetime
from typing import List, Dict, Any
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

app = FastAPI()
active_connections: List[WebSocket] = []
history: List[Dict[str, Any]] = []

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

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    await websocket.accept()
    active_connections.append(websocket)

    join_event = {
        "type": "system",
        "user": "System",
        "content": f"⚠️ {username} وارد چت شد",
        "timestamp": datetime.datetime.now().strftime("%H:%M")
    }

    for connection in active_connections:
        await connection.send_json(join_event)

    for message in history:
        await websocket.send_json(message)

    try:
        while True:
            data = await websocket.receive_text()
            chat_message = {
                "type": "chat",
                "user": username,
                "content": data,
                "timestamp": datetime.datetime.now().strftime("%H:%M")
            }
            history.append(chat_message)
            if len(history) > 100:
                history.pop(0)

            for connection in active_connections:
                await connection.send_json(chat_message)

    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)

        leave_event = {
            "type": "system",
            "user": "System",
            "content": f"❌ {username} از چت خارج شد",
            "timestamp": datetime.datetime.now().strftime("%H:%M")
        }

        for connection in active_connections:
            await connection.send_json(leave_event)

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
