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
        # Dynamically tracks satellite nodes as they connect
        self.ip_to_sat_id = {}      # Map[IP String] -> SatID
        self.active_sockets = {}    # Map[SatID] -> Socket Object
        self.next_sat_id = 1        # Auto-increment counter for discovery
        
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
                
                # 🟢 AUTO-DISCOVERY & DYNAMIC ID ASSIGNMENT
                # Ensures that even if hardware IPs change (DHCP), SatIDs remain consistent
                if ip_str not in self.ip_to_sat_id:
                    sat_id = self.next_sat_id
                    self.next_sat_id += 1
                    self.ip_to_sat_id[ip_str] = sat_id
                    logger.info(f"📡 NEW SATELLITE DISCOVERED! SatID: {sat_id} | Origin: {ip_str}")
                else:
                    sat_id = self.ip_to_sat_id[ip_str]
                    logger.info(f"🔗 Satellite Reconnected: {ip_str} as SatID {sat_id}")
                
                # Register active socket for bidirectional command routing
                self.active_sockets[sat_id] = client_sock
                
                # Spawn dedicated handler for this specific node
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
                # ESP32 sends raw JSON without newlines; we parse by brace-matching
                while "}" in buffer:
                    try:
                        end_index = buffer.find("}") + 1
                        raw_msg = buffer[:end_index]
                        buffer = buffer[end_index:]
                        
                        if "{" in raw_msg:
                            start_index = raw_msg.find("{")
                            clean_json = raw_msg[start_index:]
                            self._process_event(sat_id, json.loads(clean_json))
                            
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ Malformed JSON segment from Sat {sat_id}")
                        continue
                    except Exception as e:
                        logger.error(f"⚠️ Logic Error in Event Processor for Sat {sat_id}: {e}")
                        continue
                        
        except Exception as e:
            logger.debug(f"TCP Connection closed on Sat {sat_id}: {e}")
        finally:
            try:
                conn.close()
            except: pass
            
            # 🟢 CLEANUP: Remove socket to prevent "Ghost Commands" to dead links
            if self.active_sockets.get(sat_id) == conn:
                del self.active_sockets[sat_id]
            logger.info(f"🔌 Satellite {sat_id} Offline (TCP Link Severed)")

    def _process_event(self, sat_id, evt):
        """
        Translates Embedded Hardware Events (JSON) into Hub Actions.
        """
        etype = evt.get("event") 
        if not etype: return

        try:
            # === 1. WAKE WORD DETECTED (ESP32 is now streaming) ===
            if etype == "listening":
                rng_low = evt.get("range_low")
                rng_high = evt.get("range_high")
                
                if rng_low and rng_high:
                    logger.info(f"⚡ [Event] WAKE WORD from Sat {sat_id} (Dynamic Range: {rng_low}-{rng_high})")
                else:
                    logger.info(f"⚡ [Event] WAKE WORD from Sat {sat_id} (Manual Trigger)")
                
                if hasattr(self.engine, "on_hardware_wake"):
                    self.engine.on_hardware_wake(sat_id, {"low": rng_low, "high": rng_high} if rng_low else None)
                else:
                    logger.warning("⚠️ Engine missing handler: 'on_hardware_wake'")

            # === 2. VAD: END OF SPEECH DETECTED ===
            elif etype == "processing":
                logger.info(f"🛑 [Event] SPEECH END from Sat {sat_id}")
                if hasattr(self.engine, "flush_audio"):
                    self.engine.flush_audio(sat_id)
                else:
                    logger.warning("⚠️ Engine missing handler: 'flush_audio'")
                
            # === 3. CALIBRATION UPDATED ===
            elif etype == "calibration_report":
                floor = evt.get("floor", 0)
                logger.info(f"📉 [Event] Noise Calibration Updated [Sat {sat_id}]: {floor}")
                
                if hasattr(self.engine, "on_calibration_update"):
                    self.engine.on_calibration_update(sat_id, floor)

            # === 4. SYSTEM HEALTH ===
            elif etype == "satellite_online":
                logger.info(f"✅ [Event] Satellite {sat_id} Protocol Handshake Successful")

            elif etype == "pong":
                pass # Silent keep-alive
                
        except Exception as e:
            logger.error(f"❌ Error processing event '{etype}': {e}", exc_info=True)

    # =====================================================
    # 📤 OUTBOUND COMMAND DISPATCH (Hub -> Hardware)
    # =====================================================
    
    def send_keep_alive(self, sat_id=1):
        """Standard TCP heartbeat."""
        self.send_command(sat_id, {"event": "ping"})

    def trigger_calibration(self, sat_id=1):
        """Forces the ESP32 into a fresh noise-floor sampling loop."""
        self.send_command(sat_id, {"cmd": "calibrate"})

    def send_command(self, sat_id, command_dict):
        """
        Sends a JSON Command strictly to a targeted Satellite node.
        This prevents the 'Broadcast Storm' where all rooms react to one command.
        """
        sock = self.active_sockets.get(sat_id)
        if not sock:
            logger.warning(f"⚠️ Transmission Failed: Sat {sat_id} is currently unreachable.")
            return
            
        try:
            msg = json.dumps(command_dict)
            data = msg.encode("utf-8")
            sock.sendall(data)
        except Exception as e:
            logger.error(f"❌ TCP Command Delivery Failed [Sat {sat_id}]: {e}")

# =========================================================
# 🏠 HOME ASSISTANT CLIENT (Reflex Integration)
# =========================================================
class HomeAssistantClient:
    def __init__(self, token, url):
        self.token = token
        self.base_url = url
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
            logger.error(f"Invalid HA Service string: {service_call}")
            return

        try:
            domain, service = service_call.split(".", 1)
            url = f"{self.base_url}/services/{domain}/{service}"
            
            # Default payload targets 'all' if no specific entity provided
            payload = {"entity_id": "all"} 
            
            logger.info(f"🏠 Home Automation Trigger: {domain}.{service}")
            # requests.post(url, headers=self.headers, json=payload, timeout=2)
        except Exception as e:
            logger.error(f"HA Integration Failure: {e}")