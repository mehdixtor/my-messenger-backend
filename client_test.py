import asyncio
import websockets
import json
import aioconsole

async def chat_client(username: str):
    uri = f"ws://127.0.0.1:8000/ws/{username}"
    
    async with websockets.connect(uri) as websocket:
        print(f"--- شما با نام [{username}] به چت وصل شدید ---")
        
        async def receive_messages():
            try:
                while True:
                    message = await websocket.recv()
                    data = json.loads(message)
                    if data["type"] == "system":
                        print(f"\n[سیستم] {data['content']} ({data['timestamp']})")
                    else:
                        print(f"\n[{data['user']}]: {data['content']} ({data['timestamp']})")
                    print(">>> ", end="", flush=True)
            except Exception as e:
                print(f"\nقطع ارتباط: {e}")

        asyncio.create_task(receive_messages())

        while True:
            msg_text = await aioconsole.ainput(">>> ")
            if msg_text.lower() == 'exit':
                break
            await websocket.send(msg_text)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("لطفاً نام خود را وارد کنید. مثال: python client_test.py Mehdi")
    else:
        user_name = sys.argv[1]
        try:
            asyncio.run(chat_client(user_name))
        except KeyboardInterrupt:
            pass
