import socket
import sys
import time
import logging
import threading
import os
import subprocess

# Ensure we can find sibling modules if running from python/ root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 🔧 PHASE 3: IMPORT CORE MANAGERS
from core.pi_manager import PiManager

# 🟢 FIX: Import from the correct 'services' folder
from services.state_manager import EngineState

from services.engine import SynthetaEngine, TARGET_CHUNK_SIZE
from services.communications import SatelliteNetManager

# Import new Web UI Server Bridge
# (Local import used below in __main__)

# Configure Logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger("Main")

# ====================================================
# 📡 DISCOVERY SERVICE
# ====================================================
class DiscoveryService(threading.Thread):
    def __init__(self, port=6002):
        super().__init__(daemon=True)
        self.port = port
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self.sock.bind(('', self.port))
        except Exception as e:
            logger.error(f"❌ Failed to bind Discovery Port {self.port}: {e}")
            self.running = False

    def run(self):
        if not self.running: return
        logger.info(f"📡 Discovery Service Listening on UDP {self.port}")
        
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
                msg = data.decode('utf-8', errors='ignore').strip()
                
                if "SYNTHETA_DISCOVER" in msg:
                    logger.info(f"👋 Handshake request from {addr[0]}")
                    response = "SYNTHETA_ACK"
                    self.sock.sendto(response.encode(), addr)
            except OSError:
                break 
            except Exception as e:
                logger.error(f"Discovery Error: {e}")

# ====================================================
# 🔧 SYSTEM COMMAND HANDLERS
# ====================================================
def perform_hard_restart():
    logger.warning("🚀 SUDO RESTART COMMAND RECEIVED.")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    hub_root = os.path.dirname(current_dir) 
    
    if os.name == 'nt': 
        bat_path = os.path.join(hub_root, "run_syntheta.bat")
        cmd = ["cmd", "/c", "start", bat_path]
        try: subprocess.Popen(cmd, cwd=hub_root, shell=True)
        except Exception as e: logger.error(f"Failed to spawn launcher: {e}")
    else: 
        logger.warning("⚡ Linux Restart: Exiting with Code 42")
        sys.exit(42)
    sys.exit(0)

def cli_input_loop(comms):
    print("\n--- SYNTHETA CLI ---")
    print("Commands: [calib] [restart] [quit]")
    while True:
        try:
            cmd = input().strip().lower()
            if cmd == "calib":
                if comms: comms.trigger_calibration(1)
                else: print("❌ Comms not initialized.")
            elif cmd == "restart":
                perform_hard_restart()
            elif cmd == "quit":
                print("Exiting...")
                sys.exit(0)
        except Exception:
            pass

if __name__ == "__main__":
    print("--- SYNTHETA OMEGA BRAIN (Linux/Win Hybrid) ---")
    
    # 1. 🧠 Init Core Memory & Managers (The Brain Stem)
    try:
        # 🟢 NEW: Initialize SQLite Database & Run Boot Recovery
        from core.database_manager import DatabaseManager
        db_manager = DatabaseManager()
        db_manager.reset_processing_tasks()

        # A. Create Single Source of Truth (Memory)
        state_manager = EngineState()
        print("✅ EngineState Initialized (Single Source of Truth).")

        # B. Create Decision Maker (Injected with Memory)
        pi_manager = PiManager(state_manager)
        print("✅ PiManager Initialized (Dependency Injection Complete).")

        # C. Init Engine (Injected with both)
        engine = SynthetaEngine(state_manager, pi_manager)
        engine.on_restart_request = perform_hard_restart
        print("✅ Engine Initialized.")

        # 🟢 NEW: Initialize and Start the Night Watchman (Optional)
        # from services.memory_worker import MemoryWorker
        # worker = MemoryWorker(state_manager)
        # worker.start()

    except Exception as e:
        print(f"❌ CRITICAL: Engine Init Failed: {e}")
        sys.exit(1)

    # 2. Init Comms (TCP 5556 - The Key to ESP32)
    try:
        # This starts the TCP listener immediately
        comms = SatelliteNetManager(engine)
        
        # 🟢 CRITICAL FIX: Inject Comms back into Engine
        # This closes the loop so Engine can send commands to Satellites
        engine.register_comms(comms)
        
        print("✅ Network Manager Initialized (TCP 5556 / UDP 5555).")
        
        DiscoveryService(port=6002).start()
        
        # 🟢 NEW: Start Local Web Interface Server
        from services.web_api import get_web_server
        uvicorn_server, ws_manager = get_web_server(engine=engine, port=8000)
        #engine.register_web_manager(ws_manager)
        
        threading.Thread(target=cli_input_loop, args=(comms,), daemon=True).start()
    except Exception as e:
        print(f"❌ CRITICAL: Network Manager Failed: {e}")
        sys.exit(1)

    # 3. Bind UDP Audio Port (UDP 6000 - The Audio Pipe)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Increase buffer to prevent packet drops on high-load
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535 * 4)
        sock.bind(("0.0.0.0", 6000))
        print("✅ UDP Audio Socket Bound (Port 6000)")
    except Exception as e:
        print(f"❌ CRITICAL: Could not bind port 6000: {e}")
        sys.exit(1)

    # 4. Audio Ingestion Loop
    print("✅ System Ready. Listening on UDP 6000 and HTTP 8000...")
    
    def audio_ingest_loop():
        # Cache the queue function for speed
        engine_queue = engine.queue_audio
        
        stat_start_time = time.time()
        stat_rx_packets = 0
        stat_bytes_total = 0

        try:
            while True:
                # A. RECEIVE
                try:
                    # MTU size + headroom
                    data, addr = sock.recvfrom(4096) 
                except OSError as e:
                    logger.warning(f"UDP Recv Error: {e}")
                    continue
                
                # B. MONITOR (Every 5s)
                stat_rx_packets += 1
                stat_bytes_total += len(data)
                
                now = time.time()
                if now - stat_start_time >= 5.0:
                    elapsed = now - stat_start_time
                    rx_pps = stat_rx_packets / elapsed
                    kbps = (stat_bytes_total * 8) / 1000 / elapsed
                    if rx_pps > 1.0:
                        print(f" [📊 MONITOR] Ingest: {rx_pps:.1f} pps ({kbps:.1f} kbps)")
                    stat_start_time = now
                    stat_rx_packets = 0
                    stat_bytes_total = 0

                # C. PROCESS
                if len(data) > 0:
                    # 🟢 CRITICAL FIX: Bypass buffering loop to fix 0-Byte Bug
                    # We hardcode to ID 1 to ensure it hits the active session.
                    # If your firmware sends a header byte, change this to: sat_id = data[0]; payload = data[1:]
                    
                    sat_id = 1 
                    engine_queue(sat_id, data)

        except KeyboardInterrupt:
            print("\n--- SHUTTING DOWN AUDIO INGEST ---")
            os._exit(0)
        finally:
            sock.close()

    # Start the custom audio loop in a background daemon thread
    threading.Thread(target=audio_ingest_loop, daemon=True).start()

    # Pass the main thread to Uvicorn for dead-lock free FastAPI execution
    import asyncio
    try:
        asyncio.run(uvicorn_server.serve())
    except KeyboardInterrupt:
        print("\n--- SHUTTING DOWN SYSTEM ---")
        os._exit(0)