import asyncio
import logging
import json
import socket
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List

logger = logging.getLogger("WebGateway")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Syntheta Omega Web Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        # Map sat_id to a list of active WebSocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, sat_id: str):
        await websocket.accept()
        if sat_id not in self.active_connections:
            self.active_connections[sat_id] = []
        self.active_connections[sat_id].append(websocket)
        logger.info(f"🔗 WebUI connected to sat_{sat_id}")

    def disconnect(self, websocket: WebSocket, sat_id: str):
        if sat_id in self.active_connections and websocket in self.active_connections[sat_id]:
            self.active_connections[sat_id].remove(websocket)
            logger.info(f"❌ WebUI disconnected from sat_{sat_id}")

    async def broadcast_to_sat(self, sat_id: str, message: dict):
        if sat_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[sat_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    disconnected.append(connection)
            
            for d in disconnected:
                self.disconnect(d, sat_id)

manager = ConnectionManager()

# =========================================================
# 🌉 IPC BRIDGE MODELS & ENDPOINTS
# =========================================================

class BroadcastPayload(BaseModel):
    sat_id: str
    event_type: str
    content: str

@app.post("/internal/broadcast")
async def internal_broadcast(payload: BroadcastPayload):
    """
    IPC Route: Allows the Python Engine process to push data to the WebSockets.
    The Engine sends an HTTP POST here, and the Gateway broadcasts it to the browser.
    """
    message = {"type": payload.event_type, "content": payload.content}
    await manager.broadcast_to_sat(payload.sat_id, message)
    return {"status": "ok"}

# =========================================================
# 🌐 WEBSOCKET & HTTP ROUTES
# =========================================================

@app.websocket("/ws/sat_{sat_id}")
async def websocket_endpoint(websocket: WebSocket, sat_id: str):
    await manager.connect(websocket, sat_id)
    try:
        while True:
            # Listen for direct Web UI text input
            data = await websocket.receive_text()
            payload = json.loads(data)
            
            if payload.get("type") == "user_input":
                logger.info(f"Keyboard Input received for sat_{sat_id}: {payload.get('content')}")
                
                # IPC Route: Inject via TCP Backdoor to the Engine Process
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        # 9002 must match the WebUIInjector port in communications.py
                        s.connect(('127.0.0.1', 9002))
                        s.sendall(json.dumps(payload).encode('utf-8'))
                except Exception as e:
                    logger.error(f"Engine backdoor offline or unreachable: {e}")
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, sat_id)

@app.get("/api/vitals")
async def get_vitals():
    # Placeholder for the endpoint app.js is aggressively polling
    return {"status": "online", "cpu": 0, "memory": 0}