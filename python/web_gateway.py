import os
import asyncio
import logging
import json
import socket
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Dict, List, Any, Union
import psutil
import subprocess
import time

logger = logging.getLogger("WebGateway")
logging.basicConfig(level=logging.INFO)

# Setup paths for hosting the web UI
CURRENT_DIR   = os.path.dirname(os.path.abspath(__file__))
HUB_ROOT      = os.path.dirname(CURRENT_DIR)
WEBUI_DIR     = os.path.join(HUB_ROOT, "webui")

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

def get_vram_info():
    """Try to grab VRAM stats from nvidia-smi if available."""
    try:
        res = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=1.0
        )
        parts = res.decode().strip().split(",")
        if len(parts) == 2:
            used  = int(parts[0].strip())
            total = int(parts[1].strip())
            return f"{used}/{total} MB", round((used/total)*100, 1)
    except:
        pass
    return "0/0 MB", 0

async def tail_logs():
    """Background task to stream system logs to the WebUI."""
    log_path = os.path.join(HUB_ROOT, "assets", "logs", "syntheta.log")
    # Wait for file to exist
    while not os.path.exists(log_path):
        await asyncio.sleep(2)
    
    logger.info(f"📁 Tailing logs: {log_path}")
    with open(log_path, 'r', encoding='utf-8') as f:
        # Seek to end on startup to avoid flooding old logs
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                await asyncio.sleep(0.5)
                continue
            
            # Broadcast to all connected satellites
            msg = {"type": "engine_log", "content": line.strip()}
            for sat_id in list(manager.active_connections.keys()):
                await manager.broadcast_to_sat(sat_id, msg)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(tail_logs())

# =========================================================
# 🌉 IPC BRIDGE MODELS & ENDPOINTS
# =========================================================

class BroadcastPayload(BaseModel):
    sat_id: str
    event_type: str
    content: Any # Allow strings, dicts, or lists

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

@app.get("/")
async def get_root():
    index_path = os.path.join(WEBUI_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse(content="<h1>Syntheta UI Base</h1><p>index.html not found in webui/ folder.</p>")

@app.get("/api/vitals")
async def get_vitals():
    try:
        mem = psutil.virtual_memory()
        vram_str, vram_p = get_vram_info()
        
        return {
            "status": "online",
            "cpu": f"{psutil.cpu_percent()}%",
            "ram": f"{round(mem.used / (1024**3), 1)}/{round(mem.total / (1024**3), 1)} GB",
            "ram_pct": mem.percent,
            "vram": vram_str,
            "vram_pct": vram_p,
            "network": "Active"
        }
    except Exception as e:
        logger.error(f"Error gathering vitals: {e}")
        return {"status": "error", "cpu": "0%", "ram_pct": 0, "vram_pct": 0}

# 🟢 Mount UI assets at the root (styles.css, app.js, etc.)
if os.path.exists(WEBUI_DIR):
    app.mount("/", StaticFiles(directory=WEBUI_DIR, html=True), name="ui")