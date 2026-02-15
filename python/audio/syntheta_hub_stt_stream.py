import socket
import sys
import time
import logging
from services.engine import SynthetaEngine, TARGET_CHUNK_SIZE

# Configure Logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger("Main")

if __name__ == "__main__":
    print("--- SYNTHETA OMEGA BRAIN (WITH TELEMETRY) ---")
    
    # 1. Bind UDP Audio Port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 6000))
        # 🟢 FIX 2: Set a timeout so the loop doesn't hang forever on silence
        sock.settimeout(0.5) 
        print("✅ UDP Socket Bound (Port 6000)")
    except Exception as e:
        print(f"❌ CRITICAL: Could not bind port 6000: {e}")
        sys.exit(1)

    # 2. Init Engine
    try:
        engine = SynthetaEngine()
        print("✅ Engine Initialized. Listening...")
    except Exception as e:
        print(f"❌ CRITICAL: Engine Init Failed: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)

    # 3. Audio Ingestion Loop
    raw_buffers = {}
    
    # 📊 TELEMETRY VARS
    stat_start_time = time.time()
    stat_rx_packets = 0
    stat_proc_chunks = 0
    stat_bytes_total = 0

    try:
        while True:
            now = time.time()
            data = None
            
            # A. RECEIVE (Non-Blocking Wait)
            try:
                data, addr = sock.recvfrom(4096)
                # B. UPDATE TELEMETRY
                stat_rx_packets += 1
                stat_bytes_total += len(data)
            except socket.timeout:
                pass # Normal behavior during silence, just let the loop breathe

            # C. MONITOR REPORT (Every 5 Seconds - Guaranteed to run now)
            if now - stat_start_time >= 5.0:
                elapsed = now - stat_start_time
                rx_pps = stat_rx_packets / elapsed
                proc_cps = stat_proc_chunks / elapsed
                kbps = (stat_bytes_total * 8) / 1000 / elapsed
                
                # Check Buffer Bloat (Latency Source)
                buffer_sizes = [len(b) for b in raw_buffers.values()]
                avg_buffer = sum(buffer_sizes) / len(buffer_sizes) if buffer_sizes else 0
                
                # Only print if there is activity or if buffers are hanging
                if rx_pps > 0 or avg_buffer > 0:
                    print(f" [📊 MONITOR] Net: {rx_pps:.1f} pps ({kbps:.1f} kbps) | CPU: {proc_cps:.1f} chunks/s | Buffer Avg: {avg_buffer:.0f} bytes")
                
                # Reset
                stat_start_time = now
                stat_rx_packets = 0
                stat_proc_chunks = 0
                stat_bytes_total = 0

            # D. PROCESS
            if data and len(data) > 1:
                sat_id = data[0]
                payload = data[1:]
                
                if sat_id not in raw_buffers: 
                    raw_buffers[sat_id] = bytearray()
                
                raw_buffers[sat_id].extend(payload)
                
                # 🟢 FIX 1: THE WHILE LOOP (Eradicates Buffer Bloat)
                # Instantly drains the buffer down to 0, no matter how much data arrived
                while len(raw_buffers[sat_id]) >= TARGET_CHUNK_SIZE:
                    chunk = raw_buffers[sat_id][:TARGET_CHUNK_SIZE]
                    engine.queue_audio(sat_id, chunk)
                    raw_buffers[sat_id] = raw_buffers[sat_id][TARGET_CHUNK_SIZE:]
                    
                    stat_proc_chunks += 1 # Count processed chunks

    except KeyboardInterrupt:
        print("\n--- SHUTTING DOWN ---")
    finally:
        sock.close()