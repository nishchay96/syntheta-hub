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
        
        # Start TCP Listener for the Control Plane
        self.server_thread = threading.Thread(target=self._tcp_server_loop, daemon=True)
        self.server_thread.start()
        
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
                        # If stuck, clear buffer to prevent infinite loop
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

    def _process_event(self, sat_id, evt):
        """Translates Embedded Hardware Events (JSON) into Hub Actions."""
        etype = evt.get("event") 
        if not etype: return

        try:
            # === 1. WAKE WORD DETECTED ===
            if etype == "listening":
                rng_low = evt.get("range_low")
                rng_high = evt.get("range_high")
                
                if rng_low and rng_high:
                    logger.info(f"⚡ [Event] WAKE WORD from Sat {sat_id} (Dynamic: {rng_low}-{rng_high})")
                else:
                    logger.info(f"⚡ [Event] WAKE WORD from Sat {sat_id} (Manual)")
                
                if hasattr(self.engine, "on_hardware_wake"):
                    self.engine.on_hardware_wake(sat_id, {"low": rng_low, "high": rng_high} if rng_low else None)

            # === 2. SPEECH END ===
            elif etype == "processing":
                logger.info(f"🛑 [Event] SPEECH END from Sat {sat_id}")
                if hasattr(self.engine, "flush_audio"):
                    self.engine.flush_audio(sat_id)
                
            # === 3. CALIBRATION ===
            elif etype == "calibration_report":
                floor = evt.get("floor", 0)
                logger.info(f"📉 [Event] Calibration Updated [Sat {sat_id}]: {floor}")
                if hasattr(self.engine, "on_calibration_update"):
                    self.engine.on_calibration_update(sat_id, floor)

            elif etype == "satellite_online":
                logger.info(f"✅ [Event] Satellite {sat_id} Protocol Handshake Successful")

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
        # 🟢 FIX 1: Strip trailing slash to prevent double-slash errors
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
            
            # 🟢 FIX 2: Correct URL Construction
            # Config is '.../api/services', so we append just '/domain/service'
            url = f"{self.base_url}/{domain}/{service}"
            
            payload = {"entity_id": "all"} 
            
            logger.info(f"🏠 HA Trigger: {domain}.{service} -> {url}")
            
            # 🟢 FIX 3: Actually execute the request!
            resp = requests.post(url, headers=self.headers, json=payload, timeout=2)
            
            if resp.status_code not in [200, 201]:
                logger.warning(f"⚠️ HA Error ({resp.status_code}): {resp.text}")
                
        except Exception as e:
            logger.error(f"HA Integration Failure: {e}")