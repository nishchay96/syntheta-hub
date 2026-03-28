import socket
import json
import logging

# Configure logger (inherits settings from the main script)
logger = logging.getLogger("Emitter")

class STTEventEmitter:
    def __init__(self, host='127.0.0.1', port=9001):
        self.host = host
        self.port = port
        self._warned_unavailable = False

    def emit(self, event_type, sat_id, payload):
        data = {
            "type": event_type,
            "sat_id": sat_id,
            "payload": payload
        }
        
        # 🟢 THE FIX: The 'with' block guarantees sock.close() is called 
        # even if the Go Hub crashes mid-transmission.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0) # Fail fast (1s) if Go Hub is down
                sock.connect((self.host, self.port))
                
                # Send the message with newline delimiter
                message = json.dumps(data) + "\n"
                sock.sendall(message.encode('utf-8'))
                
                logger.debug(f"Sent Event: {event_type} -> Sat {sat_id}")
                
        except ConnectionRefusedError:
            if not self._warned_unavailable:
                logger.warning(f"Go Hub unavailable on port {self.port}; skipping emitted audio events.")
                self._warned_unavailable = True
        except socket.timeout:
            if not self._warned_unavailable:
                logger.warning(f"Go Hub on port {self.port} timed out; skipping emitted audio events.")
                self._warned_unavailable = True
        except Exception as e:
            logger.debug(f"Emitter skipped '{event_type}': {e}")
