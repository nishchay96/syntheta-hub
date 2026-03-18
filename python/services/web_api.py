import os
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn

logger = logging.getLogger("WebAPI")

# Setup paths for hosting the web UI
CURRENT_DIR   = os.path.dirname(os.path.abspath(__file__))
PYTHON_ROOT   = os.path.dirname(CURRENT_DIR)
HUB_ROOT      = os.path.dirname(PYTHON_ROOT)
WEBUI_DIR     = os.path.join(HUB_ROOT, "webui")

app = FastAPI(title="Syntheta Web UI")

# Ensure webui dir exists
os.makedirs(WEBUI_DIR, exist_ok=True)

# Mount the static files (HTML/CSS/JS)
app.mount("/static", StaticFiles(directory=WEBUI_DIR, html=True), name="static")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"🌐 WebSocket Client Connected. Total connected: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"🌐 WebSocket Client Disconnected. Total connected: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"⚠️ Failed to send WS message: {e}")

manager = ConnectionManager()

# Default Endpoint to serve the main HTML file
@app.get("/")
async def get_root():
    index_path = os.path.join(WEBUI_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Syntheta UI Base</h1><p>index.html not found in webui/ folder.</p>")

# Serves CSS/JS directly (already mounted via StaticFiles, this is a fallback)
@app.get("/styles.css")
async def get_styles():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(WEBUI_DIR, "styles.css"), media_type="text/css")

@app.get("/app.js")
async def get_js():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(WEBUI_DIR, "app.js"), media_type="application/javascript")

# ── Vitals endpoint ───────────────────────────────────────
@app.get("/api/vitals")
async def get_vitals():
    vitals = {"vram": "N/A", "vram_pct": 0, "ram": "N/A", "ram_pct": 0, "network": "OK"}
    try:
        import psutil
        mem = psutil.virtual_memory()
        vitals["ram"] = f"{mem.used / (1024**3):.1f} GB"
        vitals["ram_pct"] = round(mem.percent, 1)
    except ImportError:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            used  = torch.cuda.memory_allocated() / (1024**3)
            total = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            vitals["vram"] = f"{used:.1f} / {total:.1f} GB"
            vitals["vram_pct"] = round(used / total * 100, 1)
    except Exception:
        pass
    return vitals

# WebSocket Endpoint
@app.websocket("/ws/sat_0")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Wait for JSON commands from the frontend UI
            data = await websocket.receive_json()
            
            if data.get("type") == "user_input":
                content = data.get("content", "")
                if content and syntheta_engine_ref:
                    logger.info(f"UI Command Received: {content}")
                    
                    # 🟢 Inject directly into Syntheta Engine
                    # We run it in a thread so it doesn't block the async ASGI loop
                    import threading
                    import time
                    threading.Thread(
                        target=syntheta_engine_ref.handle_input, 
                        args=(0, content, {"start_time": time.perf_counter()})
                    ).start()
                    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
        manager.disconnect(websocket)

# ----------------------------------------------------
# LAUNCHER
# ----------------------------------------------------
def get_web_server(engine=None, port=8000):
    """
    Returns the Uvicorn server and the WebSocket manager.
    The caller MUST `import asyncio; asyncio.run(server.serve())` on the MAIN thread.
    """
    global syntheta_engine_ref
    syntheta_engine_ref = engine
    
    logger.info(f"✨ UI Server configured on http://0.0.0.0:{port}")
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    
    return server, manager
