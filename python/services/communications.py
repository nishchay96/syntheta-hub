import socket
import json
import logging
import threading
import time
import requests

logger = logging.getLogger("Comms")

# =========================================================
# 📡 SATELLITE NETWORK MANAGER
# =========================================================
class SatelliteNetManager:
    """
    The Direct Link to Syntheta Hardware (Alpha/Gamma Nodes).
    - TCP Lane (Control): Receives Wake Words, Sends Commands (Port 5556).
    - UDP Audio: Handled externally by the Go Bridge (Omega Lane 1).
    """
    def __init__(self, engine_ref, tcp_port=5556):
        self.engine = engine_ref
        self.tcp_port = tcp_port
        self.running = True
        
        # 🟢 MULTI-NODE STATE MANAGEMENT
        self.ip_to_sat_id = {}      # Map[IP String] -> SatID
        self.active_sockets = {}    # Map[SatID] -> Socket Object
        self.next_sat_id = 1        # Auto-increment counter
        
        # Start TCP Listener for the Control Plane (Hardware Nodes)
        self.server_thread = threading.Thread(target=self._tcp_server_loop, daemon=True)
        self.server_thread.start()

        # 🟢 FIX: Create a persistent link to the Go Bridge (Omega Internal)
        # This allows Python to receive 'playback_finished' events from the audio relay.
        self.go_link_thread = threading.Thread(target=self._go_bridge_link_loop, daemon=True)
        self.go_link_thread.start()
        
    def _tcp_server_loop(self):
        """Host the TCP Control Server (Port 5556)"""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            server.bind(("0.0.0.0", self.tcp_port))
            server.listen(5)
            logger.info(f"✅ TCP Control Server listening on {self.tcp_port}")
        except Exception as e:
            logger.error(f"❌ Failed to bind TCP Control Plane: {e}")
            return

        while self.running:
            try:
                client_sock, addr = server.accept()
                ip_str = addr[0]
                
                # 🟢 AUTO-DISCOVERY
                if ip_str not in self.ip_to_sat_id:
                    sat_id = self.next_sat_id
                    self.next_sat_id += 1
                    self.ip_to_sat_id[ip_str] = sat_id
                    logger.info(f"📡 NEW SATELLITE DISCOVERED! SatID: {sat_id} | Origin: {ip_str}")
                else:
                    sat_id = self.ip_to_sat_id[ip_str]
                    logger.info(f"🔗 Satellite Reconnected: {ip_str} as SatID {sat_id}")
                
                self.active_sockets[sat_id] = client_sock
                threading.Thread(target=self._handle_tcp_client, args=(client_sock, sat_id), daemon=True).start()
                
            except Exception as e:
                logger.error(f"Server Accept Error: {e}")

    def _go_bridge_link_loop(self):
        """
        Persistent client connection to the Go Dispatcher (Port 9001).
        Synchronizes playback state by listening for 'playback_finished'.
        """
        while self.running:
            try:
                # 🟢 FIX: Connect to Go's internal event broadcaster
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(None) # Blocking read is fine here
                    s.connect(("127.0.0.1", 9001))
                    logger.info("🔗 Linked to Go Bridge (Omega Feedback Loop Active)")
                    
                    # Process incoming JSON stream
                    f = s.makefile('r', encoding='utf-8')
                    while self.running:
                        line = f.readline()
                        if not line: break
                        try:
                            # Pass events to the common processor
                            self._process_event(1, json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except (ConnectionRefusedError, socket.timeout, OSError):
                # Wait for Go Bridge if it's not yet ready
                time.sleep(2)
            except Exception as e:
                logger.debug(f"Go Bridge Link Connection Error: {e}")
                time.sleep(2)

    def _handle_tcp_client(self, conn, sat_id):
        """Process incoming JSON command streams from ESP32/Alpha."""
        buffer = ""
        conn.settimeout(None) 
        
        try:
            while self.running:
                data = conn.recv(1024)
                if not data: break
                
                buffer += data.decode("utf-8", errors="ignore")
                
                # 🟢 ROBUST JSON STREAM PARSING
                while "}" in buffer:
                    try:
                        if "{" not in buffer: break # Safety check
                        start_index = buffer.find("{")
                        end_index = buffer.find("}", start_index) + 1
                        
                        if end_index == 0: break # Incomplete JSON
                        
                        raw_msg = buffer[start_index:end_index]
                        buffer = buffer[end_index:] # Advance buffer
                        
                        self._process_event(sat_id, json.loads(raw_msg))
                            
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ Malformed JSON segment from Sat {sat_id}")
                        if len(buffer) > 2048: buffer = ""
                        continue
                    except Exception as e:
                        logger.error(f"⚠️ Event Logic Error: {e}")
                        continue
                        
        except Exception as e:
            logger.debug(f"TCP Connection closed on Sat {sat_id}: {e}")
        finally:
            try:
                conn.close()
            except: pass
            
            if self.active_sockets.get(sat_id) == conn:
                del self.active_sockets[sat_id]
            logger.info(f"🔌 Satellite {sat_id} Offline (TCP Link Severed)")

# ... previous code (handle_tcp_client and _process_event) ...

    def _process_event(self, sat_id, evt):
        """Translates Embedded Hardware or internal Go events into Hub Actions."""
        etype = evt.get("event") or evt.get("type")
        if not etype: return

        try:
            if etype == "listening":
                rng_low = evt.get("range_low")
                rng_high = evt.get("range_high")
                if hasattr(self.engine, "on_hardware_wake"):
                    self.engine.on_hardware_wake(sat_id, {"low": rng_low, "high": rng_high} if rng_low else None)

            elif etype == "processing":
                if hasattr(self.engine, "flush_audio"):
                    self.engine.flush_audio(sat_id)
                
            elif etype == "calibration_report":
                floor = evt.get("floor", 0)
                if hasattr(self.engine, "on_calibration_update"):
                    self.engine.on_calibration_update(sat_id, floor)

            elif etype == "playback_finished":
                target_sat = evt.get("sat_id", sat_id)
                payload = evt.get("payload", {})
                filename = payload.get("file", "unknown_file")
                logger.info(f"✅ [Event] Playback Finished on Sat {target_sat}: {filename}")
                if hasattr(self.engine, "on_playback_finished"):
                    self.engine.on_playback_finished(target_sat, filename)

            elif etype == "satellite_online":
                logger.info(f"✅ [Event] Satellite {sat_id} Protocol Handshake Successful")
                if hasattr(self.engine, "emitter"):
                    # 🟢 FIX: Delay the audio by 1 second to let hardware settle
                    def delayed_warning():
                        time.sleep(1.0) 
                        self.engine.emitter.emit("play_file", sat_id, {"filepath": "assets/system/satellite_connect.wav"})
                    threading.Thread(target=delayed_warning, daemon=True).start()
        except Exception as e:
            logger.error(f"❌ Error processing event '{etype}': {e}", exc_info=True)

    # =====================================================
    # 📤 OUTBOUND COMMAND DISPATCH
    # =====================================================
    def send_keep_alive(self, sat_id=1):
        self.send_command(sat_id, {"event": "ping"})

    def trigger_calibration(self, sat_id=1):
        self.send_command(sat_id, {"cmd": "calibrate"})

    def send_command(self, sat_id, command_dict):
        sock = self.active_sockets.get(sat_id)
        if not sock: return
        try:
            msg = json.dumps(command_dict)
            data = msg.encode("utf-8")
            sock.sendall(data)
        except Exception as e:
            logger.error(f"❌ TCP Delivery Failed [Sat {sat_id}]: {e}")

# =========================================================
# 🏠 HOME ASSISTANT CLIENT
# =========================================================
class HomeAssistantClient:
    def __init__(self, token, url):
        self.token = token
        self.base_url = url.rstrip('/') 
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
    
    def execute(self, service_call):
        """
        Executes a Home Assistant Service.
        Format: "domain.service" (e.g. "light.turn_on")
        """
        if not service_call or "." not in service_call:
            logger.error(f"Invalid HA Service: {service_call}")
            return

        try:
            domain, service = service_call.split(".", 1)
            url = f"{self.base_url}/api/services/{domain}/{service}"
            payload = {"entity_id": "all"} 
            
            logger.info(f"🏠 HA Trigger: {domain}.{service} -> {url}")
            resp = requests.post(url, headers=self.headers, json=payload, timeout=2)
            
            if resp.status_code not in [200, 201]:
                logger.warning(f"⚠️ HA Error ({resp.status_code}): {resp.text}")
                
        except Exception as e:
            logger.error(f"HA Integration Failure: {e}")

class WebUIInjector(threading.Thread):
    """Listens on 9002 for direct text injection from the Web UI."""
    def __init__(self, engine_ref):
        super().__init__(daemon=True)
        self.engine = engine_ref
        self.port = 9002

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', self.port))
        server.listen(5)
        logger.info(f"🕸️ Web UI Injector Backdoor open on TCP {self.port}")
        
        while True:
            conn, _ = server.accept()
            with conn:
                data = conn.recv(2048)
                if data:
                    try:
                        payload = json.loads(data.decode('utf-8'))
                        sat_id = int(payload.get('sat_id', 1))
                        text = payload.get('content', '')
                        
                        logger.info(f">>> 📝 WEB INPUT [Sat {sat_id}]: '{text}'")
                        
                        # Route this directly into your cognitive loop.
                        # Replace `self.engine.process_text_pipeline` with the exact 
                        # function your engine uses when Whisper yields a final string.
                        self.engine.handle_input(sat_id, text)
                    except Exception as e:
                        logger.error(f"Failed to inject web text: {e}")