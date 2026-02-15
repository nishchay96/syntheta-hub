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
    The Direct Link to Syntheta Hardware.
    - TCP Lane (Control): Receives Wake Words, Sends Commands (Port 5556).
    - UDP Audio is now purely handled by the Go Bridge.
    """
    def __init__(self, engine_ref, tcp_port=5556):
        self.engine = engine_ref
        self.tcp_port = tcp_port
        self.running = True
        
        # 🟢 NEW: Multi-Node State Management
        self.ip_to_sat_id = {}      # Map[IP String] -> SatID
        self.active_sockets = {}    # Map[SatID] -> Socket Object
        self.next_sat_id = 1        # Auto-increment counter
        
        # Start TCP Listener (For Receiving Commands UP from ESP32)
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
            logger.error(f"❌ Failed to bind TCP: {e}")
            return

        while self.running:
            try:
                client_sock, addr = server.accept()
                ip_str = addr[0]
                
                # 🟢 AUTO-DISCOVERY & DYNAMIC ID ASSIGNMENT
                if ip_str not in self.ip_to_sat_id:
                    sat_id = self.next_sat_id
                    self.next_sat_id += 1
                    self.ip_to_sat_id[ip_str] = sat_id
                    logger.info(f"📡 NEW SATELLITE DISCOVERED (TCP)! Assigned SatID: {sat_id} | Origin: {ip_str}")
                else:
                    sat_id = self.ip_to_sat_id[ip_str]
                    logger.info(f"🔗 Satellite Reconnected (TCP): {ip_str} as SatID {sat_id}")
                
                # Register active socket for routing commands back down
                self.active_sockets[sat_id] = client_sock
                
                # Handle this client in a separate thread
                threading.Thread(target=self._handle_tcp_client, args=(client_sock, sat_id), daemon=True).start()
                
            except Exception as e:
                logger.error(f"Server Accept Error: {e}")

    def _handle_tcp_client(self, conn, sat_id):
        """Process incoming JSON streams (Robust to missing newlines)"""
        buffer = ""
        conn.settimeout(None) 
        
        try:
            while self.running:
                data = conn.recv(1024)
                if not data: break
                
                buffer += data.decode("utf-8", errors="ignore")
                
                # PARSE JSON OBJECTS BY BRACES (ESP sends raw JSON, no \n)
                while "}" in buffer:
                    try:
                        end_index = buffer.find("}") + 1
                        raw_msg = buffer[:end_index]
                        buffer = buffer[end_index:] # Shift buffer
                        
                        # Clean up any leading garbage (if any)
                        if "{" in raw_msg:
                            start_index = raw_msg.find("{")
                            clean_json = raw_msg[start_index:]
                            self._process_event(sat_id, json.loads(clean_json))
                            
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ Malformed JSON from Sat {sat_id}")
                        continue
                    except Exception as e:
                        logger.error(f"⚠️ Logic Error in Event Processor: {e}")
                        continue
                        
        except Exception as e:
            logger.error(f"TCP Connection Error on Sat {sat_id}: {e}")
        finally:
            try:
                conn.close()
            except: pass
            
            # 🟢 CLEANUP: Remove socket on disconnect so we don't send to dead links
            if self.active_sockets.get(sat_id) == conn:
                del self.active_sockets[sat_id]
            logger.info(f"🔌 Satellite {sat_id} Disconnected")

    def _process_event(self, sat_id, evt):
        """
        The Brain of the Nervous System.
        Translates ESP32 JSON -> Python Actions.
        """
        etype = evt.get("event") 
        if not etype: return

        # SAFEGUARD: Wrap callbacks to prevent thread crash
        try:
            # === 1. WAKE WORD DETECTED (ESP says: "listening") ===
            if etype == "listening":
                rng_low = evt.get("range_low")
                rng_high = evt.get("range_high")
                
                if rng_low and rng_high:
                    logger.info(f"⚡ [Event] WAKE WORD from Sat {sat_id} (Range: {rng_low}-{rng_high})")
                else:
                    logger.info(f"⚡ [Event] WAKE WORD from Sat {sat_id} (Session Active)")
                
                if hasattr(self.engine, "on_hardware_wake"):
                    if rng_low:
                        self.engine.on_hardware_wake(sat_id, {"low": rng_low, "high": rng_high})
                    else:
                        self.engine.on_hardware_wake(sat_id)
                else:
                    logger.warning("⚠️ Engine missing 'on_hardware_wake'")

            # === 2. USER STOPPED SPEAKING ===
            elif etype == "processing":
                logger.info(f"🛑 [Event] END STREAM from Sat {sat_id}")
                if hasattr(self.engine, "flush_audio"):
                    self.engine.flush_audio(sat_id)
                else:
                    logger.warning("⚠️ Engine missing 'flush_audio'")
                
            # === 3. CALIBRATION REPORT ===
            elif etype == "calibration_report":
                floor = evt.get("floor", 0)
                logger.info(f"📉 [Event] Noise Floor Updated: {floor}")
                
                if hasattr(self.engine, "on_calibration_update"):
                    self.engine.on_calibration_update(sat_id, floor)

            # === 4. HANDSHAKE ===
            elif etype == "satellite_online":
                logger.info(f"✅ [Event] Satellite {sat_id} Confirmed Online")

            # === 5. PONG ===
            elif etype == "pong":
                pass # Keep-alive ack
                
        except Exception as e:
            logger.error(f"❌ Error processing event '{etype}': {e}", exc_info=True)

    # =====================================================
    # 📤 OUTBOUND METHODS
    # =====================================================
    
    def send_keep_alive(self, sat_id=1):
        """Sends a Ping to check connection"""
        self.send_command(sat_id, {"event": "ping"})

    def trigger_calibration(self, sat_id=1):
        """Manually trigger ESP32 calibration"""
        self.send_command(sat_id, {"cmd": "calibrate"})

    def send_command(self, sat_id, command_dict):
        """Send JSON Command via TCP strictly to the target Satellite"""
        # 🟢 TARGETED ROUTING: Eliminates the multi-node broadcast bug
        sock = self.active_sockets.get(sat_id)
        if not sock:
            logger.warning(f"⚠️ Cannot send command to Sat {sat_id}: Device offline or unknown.")
            return
            
        try:
            msg = json.dumps(command_dict)
            data = msg.encode("utf-8")
            sock.sendall(data)
        except Exception as e:
            logger.error(f"❌ Failed to send TCP command to Sat {sat_id}: {e}")

# =========================================================
# 🏠 HOME ASSISTANT CLIENT
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
            logger.error(f"Invalid HA Service: {service_call}")
            return

        try:
            domain, service = service_call.split(".", 1)
            url = f"{self.base_url}/{domain}/{service}"
            
            payload = {"entity_id": "all"} 
            
            logger.info(f"🏠 Calling HA: {domain}.{service}")
            # response = requests.post(url, headers=self.headers, json=payload, timeout=2)
        except Exception as e:
            logger.error(f"HA Call Failed: {e}")