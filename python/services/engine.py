import threading
import time
import json
import wave
import logging
import sys
import os
import re
import urllib.request
from datetime import datetime
import requests

# ============================================
# 🔧 SERVICE IMPORTS (ROBUST PATHING)
# ============================================
current_dir   = os.path.dirname(os.path.abspath(__file__))
python_root   = os.path.abspath(os.path.join(current_dir, '..'))
hub_root      = os.path.abspath(os.path.join(python_root, '..'))
audio_lib_dir = os.path.join(python_root, 'audio')

if audio_lib_dir not in sys.path: sys.path.insert(0, audio_lib_dir)
if python_root   not in sys.path: sys.path.insert(0, python_root)
if hub_root      not in sys.path: sys.path.insert(0, hub_root)

try:
    from stt_event_emitter import STTEventEmitter
except ImportError:
    try:
        from python.audio.stt_event_emitter import STTEventEmitter
    except ImportError:
        from audio.stt_event_emitter import STTEventEmitter

from core.context_assembler import ContextAssembler
from core.database_manager  import DatabaseManager
from core.gatekeeper        import AudioGatekeeper
from nlu.llm_bridge         import OllamaBridge
from nlu.router_bridge      import LibrarianRouter
from core.pi_manager        import PiManager
from .state_manager         import EngineState
from .transcriber           import AudioTranscriber
from .config                import *
from .communications        import HomeAssistantClient, WebUIInjector
from .audio_tools           import pad_audio_file
from tts_engine             import TTSEngine
from .memory_worker         import MemoryWorker
from nlu.api_scout          import APIScout

logger = logging.getLogger("SynthetaEngine")

# ============================================
# TUNING PARAMETERS
# ============================================
LIMIT_REFLEX              = 40.0
LIMIT_CONVERSATION        = 120.0
SESSION_IDLE_TIMEOUT      = 10.0
WAKE_COLLISION_SKIP_BYTES = 16000
PCM_BYTES_PER_SEC         = 32000
UDP_PAYLOAD_SIZE          = 1024
PACKETS_PER_SEC           = PCM_BYTES_PER_SEC / UDP_PAYLOAD_SIZE

DEFAULT_HALLUCINATIONS = [
    "you", "thank you", "thanks", "start", "stop", "no", "yes",
    "subtitles", "copyright", "audio", "video", "subscribe",
    "watching", "bye", "amara", "org"
]


# ============================================
# 🧠 MAIN ENGINE
# ============================================
class SynthetaEngine:
    def __init__(self, state_manager, pi_manager):
        logger.info("⚡ Initializing Syntheta Engine (Omega v3)...")

        self.state = state_manager
        self.pi    = pi_manager
        self.comms = None

        self.conversation_windows = {}   

        self.db = DatabaseManager()
        self.ha = HomeAssistantClient(HA_TOKEN, HA_URL)

        self.emitter     = STTEventEmitter()
        self.assembler   = ContextAssembler(self.db)
        self.transcriber = AudioTranscriber()
        self.llm         = OllamaBridge()
        self.gatekeeper  = AudioGatekeeper()

        try:
            self.tts = TTSEngine()
            logger.info("✅ TTS Engine Online")
        except Exception as e:
            logger.warning(f"⚠️ TTS Disabled: {e}")
            self.tts = None

        self.nightwatchman = MemoryWorker(self.state, on_memory_changed=lambda sid, user: self._broadcast_memory_matrix(sid, user))
        self.nightwatchman.start()

        self.librarian = LibrarianRouter()
        self.librarian.vault_path = self.nightwatchman.vault_path
        self.nightwatchman.capture.router = self.librarian
        
        self.api_scout = APIScout()
        self.librarian.api_scout = self.api_scout

        self.web_injector = WebUIInjector(self)
        self.web_injector.start()

        self.security_mode           = "NORMAL"
        self.sudo_timer              = 0
        self.sudo_challenge_deadline = 0
        self.pending_sudo_cmd        = None
        self.hallucinations          = list(DEFAULT_HALLUCINATIONS)
        self.ghost_counters          = {}
        self.wwd_timers              = {}
        self.pending_force_listen    = {}
        self.is_thinking             = False
        
        # Debounce for Memory Matrix Broadcasts
        self._last_matrix_broadcast  = 0.0
        self._last_matrix_data       = None
        self._matrix_cooldown        = 5.0
        
        # Debounce for Memory Matrix Broadcasts
        self._last_matrix_broadcast  = 0
        self._matrix_cooldown        = 5.0
        
        threading.Thread(target=self._processing_loop,     daemon=True).start()
        threading.Thread(target=self._monitor_loop,        daemon=True).start()
        threading.Thread(target=self._sudo_heartbeat_loop, daemon=True).start()

        self._play_boot_sound()
        
        # 🔥 Hot-load models into VRAM immediately
        threading.Thread(target=self.librarian.pre_load, daemon=True).start()
        threading.Thread(target=self.llm.pre_load,       daemon=True).start()
        
        logger.info("🟢 ENGINE READY.")

    def _extract_requested_count(self, text: str) -> int | None:
        if not text:
            return None
        match = re.search(r"\btop\s+(\d{1,2})\b", text.lower())
        if match:
            return int(match.group(1))
        match = re.search(r"\b(\d{1,2})\s+(?:headlines|news items|items)\b", text.lower())
        if match:
            return int(match.group(1))
        return None

    def _direct_counted_live_reply(self, text: str, packet: dict) -> str | None:
        requested = self._extract_requested_count(text)
        memory_tank = packet.get("memory_tank") or ""
        route = packet.get("route_taken")
        if not requested or route != "general_web_search" or "--- LIVE WEB" not in memory_tank:
            return None

        bullets = []
        for line in memory_tank.splitlines():
            stripped = line.strip()
            if stripped.startswith("• ") and not stripped.startswith("• Source:"):
                bullets.append(stripped[2:].strip())
        if len(bullets) < requested:
            return None

        numbered = [f"{i}. {item}" for i, item in enumerate(bullets[:requested], start=1)]
        return "Here are the top {} items for today: {}".format(requested, " ".join(numbered))

    def _direct_live_failure_reply(self, text: str, packet: dict) -> str | None:
        if not packet.get("live_lookup_failed"):
            return None
        query = packet.get("failed_web_query") or text
        return f"I couldn't fetch live current data for '{query}' right now because web retrieval is unavailable. Please try again later."

    def _direct_weather_location_reply(self, packet: dict) -> str | None:
        if packet.get("web_data") != "__ASK_WEATHER_LOCATION__":
            return None
        return "I couldn't detect your location for weather yet. Tell me your city, and I'll fetch the weather, AQI, rain, and forecast."

    def _direct_weather_unavailable_reply(self, packet: dict) -> str | None:
        if packet.get("web_data") != "__WEATHER_UNAVAILABLE__":
            return None
        return "I couldn't fetch weather data for that location right now. Tell me your city again in a moment, and I'll try the weather, AQI, rain, and forecast."

    def register_comms(self, comms_instance):
        self.comms = comms_instance
        logger.info("🔗 Network Manager Registered.")

    def emit_to_webui(self, sat_id, event_type, content):
        try:
            requests.post(
                "http://127.0.0.1:8001/internal/broadcast",
                json={
                    "sat_id": str(sat_id),
                    "event_type": event_type,
                    "content": content 
                },
                # 🟢 FIX: Increased timeout from 0.2s to 1.0s to allow the matrix payload to pass
                timeout=1.0 
            )
        except Exception as e:
            # 🟢 FIX: Added temporary error logging so we don't fail silently
            logger.error(f"⚠️ WebUI Broadcast Failed: {e}")    
    def _play_boot_sound(self):
        boot_wav = os.path.join(
            os.path.dirname(__file__), '../../assets/system/boot.wav')
        if os.path.exists(boot_wav):
            self._speak_file(1, boot_wav)

    # =========================================
    # WEB UI MATRIX BROADCASTER
    # =========================================
    def _broadcast_memory_matrix(self, sat_id: int, username: str):
        """Fetches SQL core memory and builds a graph for the D3.js UI."""
        # 🟢 DEBOUNCE: Prevent duplicate animations if called in rapid succession
        now = time.time()
        if now - self._last_matrix_broadcast < self._matrix_cooldown:
            return
        self._last_matrix_broadcast = now

        try:
            # 🟢 FIX: Ensure username is lowercase to match database partition
            username_clean = username.lower()
            raw_facts = self.db.get_all_core_facts(username_clean)
            memory_graph = {}
            
            for key, data in raw_facts.items():
                bucket = data.get("bucket", "General")
                # Convert "opinions.dark_chocolate" -> "Dark Chocolate"
                entity_name = key.split(".")[-1].replace("_", " ").title()
                
                if bucket not in memory_graph:
                    memory_graph[bucket] = []
                if entity_name not in memory_graph[bucket]:
                    memory_graph[bucket].append(entity_name)

            # 🟢 DATA-AWARE BROADCAST: Only emit if the actual structure changed
            if self._last_matrix_data == memory_graph:
                return
            self._last_matrix_data = memory_graph

            # Broadcast the assembled graph
            payload = {
                "user": username, # Keep original case for UI display 
                "data": memory_graph
            }
            
            # 🟢 FIX: Broadcast to both the target Satellite AND the Web UI (Sat 0)
            self.emit_to_webui(sat_id, "profile_loaded", payload)
            if str(sat_id) != "0":
                self.emit_to_webui(0, "profile_loaded", payload)
                
            logger.info(f"🌐 Broadcasted Memory Matrix for '{username}' ({len(raw_facts)} nodes)")
        except Exception as e:
            logger.error(f"⚠️ Failed to broadcast memory matrix: {e}")


    # =========================================
    # NOMIC VECTOR
    # =========================================
    def _get_nomic_vector(self, text):
        try:
            payload = {"model": "nomic-embed-text:v1.5", "prompt": text, "keep_alive": -1}
            req = urllib.request.Request(
                "http://localhost:11434/api/embeddings",
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5.0) as res:
                return json.loads(res.read().decode('utf-8'))['embedding']
        except Exception as e:
            logger.error(f"⚠️ Vector Gen Failed: {e}")
            return None

    # =========================================
    # EVENT HANDLERS
    # =========================================
    def on_hardware_wake(self, sat_id, payload=None):
        logger.info(f">>> ⚡ HARDWARE WAKE: Satellite {sat_id}")

        self.emitter.emit("stop_audio", sat_id, {})

        if sat_id in self.wwd_timers:
            self.wwd_timers[sat_id].cancel()
            del self.wwd_timers[sat_id]

        self.state.snapshot_playback(sat_id)
        self.state.session_origins[sat_id]  = "barge_in"
        self.ghost_counters[sat_id]         = 0

        with self.state.audio_queue.mutex:
            self.state.audio_queue.queue.clear()

        self.state.deaf_until                = 0.0
        self.state.skip_byte_counter         = WAKE_COLLISION_SKIP_BYTES
        self.state.audio_buffers[sat_id]     = b""
        self.pending_force_listen[sat_id]    = False
        self.state.session_start_time        = time.time()
        self.state.is_conversation           = False
        self.state.last_active_time[sat_id]  = time.time()
        self.state.session_mode[sat_id]      = "LISTENING"

        # 🟢 FIX: Initialize the NightWatchman vault to the currently active user
        active_user = self.state.get_active_user(sat_id)
        self.nightwatchman.capture.set_user(active_user, sat_id)
        
        # 🟢 FIX: Immediate Sync of Vault Files to Database
        if active_user and active_user != "Guest":
            user_dir = self.nightwatchman.capture._get_user_dir(sat_id)
            self.nightwatchman._sync_vault_to_sql(user_dir, "Hardware Wake Sync")
            self._broadcast_memory_matrix(sat_id, active_user)
            
        self.pi.start_new_session(sat_id)

    def on_calibration_update(self, sat_id, floor):
        self.gatekeeper.update_calibration(sat_id, floor)

    def on_playback_finished(self, sat_id, filename):
        base_file      = os.path.basename(filename)
        is_interrupted = self.state.resume_pending.get(sat_id, False)

        logger.info(f"🎵 Playback Finished [Sat {sat_id}]: {base_file}")

        if not is_interrupted and "temp" in filename:
            try:
                if os.path.exists(filename):
                    os.remove(filename)
            except Exception:
                pass

        self.state.clear_playback(sat_id)

        if "satellite_connect" in base_file:
            time.sleep(1.5)
            if self.comms:
                self.comms.send_command(sat_id, {"cmd": "calibrate"})
            return

        if self.pending_force_listen.get(sat_id):
            self.state.audio_buffers[sat_id]    = b""
            self.state.deaf_until               = 0.0
            self.state.last_active_time[sat_id] = time.time()
            self.state.session_mode[sat_id]     = "LISTENING"
            self.state.skip_byte_counter        = 0
            if self.comms:
                threading.Timer(
                    0.5,
                    lambda: self.comms.send_command(
                        sat_id, {"cmd": "force_listen", "timeout": 4})
                ).start()
            self.pending_force_listen[sat_id] = False

    def queue_audio(self, sat_id, pcm):
        self.state.last_active_time[sat_id] = time.time()
        if self.state.session_mode.get(sat_id) == "LISTENING":
            try:
                self.state.audio_queue.put_nowait((sat_id, pcm))
            except Exception as e:
                logger.error(f"❌ Queue Full: {e}")

    def flush_audio(self, sat_id):
        self._transcribe(sat_id)

    # =========================================
    # CORE LOOPS
    # =========================================
    def _processing_loop(self):
        while True:
            try:
                sat_id, pcm = self.state.audio_queue.get()
                self._process_audio_chunk(sat_id, pcm)
                self.state.audio_queue.task_done()
            except Exception:
                time.sleep(0.1)

    def _monitor_loop(self):
        while True:
            time.sleep(0.5)
            now = time.time()
            if self.security_mode == "SUDO_CHALLENGE":
                if now > self.sudo_challenge_deadline:
                    self.security_mode = "NORMAL"
                    self._speak(1, "Login timeout.")
            for sat_id, mode in list(self.state.session_mode.items()):
                if mode != "LISTENING":
                    continue
                if self.security_mode == "SUDO_SESSION":
                    continue
                limit = (LIMIT_CONVERSATION
                         if getattr(self.state, 'is_conversation', False)
                         else LIMIT_REFLEX)
                start_t = getattr(self.state, 'session_start_time', now)
                if (now - start_t) > limit:
                    self._close_session(sat_id)
                    continue
                if (now - self.state.last_active_time.get(sat_id, now)) > SESSION_IDLE_TIMEOUT:
                    self._close_session(sat_id)

    def _sudo_heartbeat_loop(self):
        while True:
            if self.security_mode == "SUDO_SESSION" and self.comms:
                self.comms.send_keep_alive(1)
            time.sleep(25)

    def _close_session(self, sat_id):
        self.state.session_mode[sat_id] = "IDLE"
        self.state.deaf_until = 0.0

    # =========================================
    # AUDIO PIPELINE
    # =========================================
    def _process_audio_chunk(self, sat_id, pcm):
        if time.time() < self.state.deaf_until:
            return
        if hasattr(self.state, 'skip_byte_counter') and self.state.skip_byte_counter > 0:
            skip = min(len(pcm), self.state.skip_byte_counter)
            self.state.skip_byte_counter -= skip
            if skip == len(pcm):
                return
            pcm = pcm[skip:]
        if sat_id not in self.state.audio_buffers:
            self.state.audio_buffers[sat_id] = b""
        self.state.audio_buffers[sat_id] += pcm

    def _transcribe(self, sat_id):
        audio_data = self.state.audio_buffers.get(sat_id, b"")[:]
        self.state.audio_buffers[sat_id] = b""
        if len(audio_data) < 3200:
            return
        if not self.gatekeeper.is_speech(sat_id, audio_data):
            self._close_session(sat_id)
            return
        threading.Thread(
            target=self._run_pipeline, args=(sat_id, audio_data)).start()

    def _run_pipeline(self, sat_id, audio_bytes):
        try:
            text, confidence, turn_telemetry = self.transcriber.transcribe(audio_bytes)
            turn_telemetry["start_time"] = time.perf_counter()

            if not text or len(text) < 2 or confidence < 0.4:
                return
            if text.lower() in self.hallucinations:
                return

            logger.info(f">>> 📝 INPUT: '{text}'")
            self.state.last_active_time[sat_id] = time.time()
            self.emit_to_webui(sat_id, "stt_transcription", text)

            if self.security_mode == "SUDO_CHALLENGE":
                if "sudo login" in text.lower():
                    self._enter_sudo_calibration(sat_id)
                return
            if self.security_mode == "SUDO_SESSION":
                self._handle_sudo_command(sat_id, text)
                return

            self._handle_normal_command(sat_id, text, turn_telemetry)

        except Exception as e:
            logger.error(f"❌ Pipeline crashed: {e}", exc_info=True)
            self._speak(sat_id, "I hit an internal error.")
        finally:
            self.is_thinking = False

    # =========================================
    # COMPOUND COMMAND SPLITTER
    # =========================================
    COMPOUND_PROTECT = {
        "salt and pepper", "pros and cons", "back and forth",
        "bread and butter", "on and off", "up and down",
        "left and right", "black and white", "now and then",
        "trial and error", "give and take", "more and more",
    }

    SPLIT_PATTERN = re.compile(
        r',\s*and\s+'       
        r'|,\s*also\s+'     
        r'|,\s*then\s+'     
        r'|\band\s+also\s+' 
        r'|\.\s+'           
        r'|,\s*(?=[a-z])',  
        re.IGNORECASE
    )

    def _split_compound_input(self, text):
        if len(text.split()) <= 5:
            return [text]

        text_lower = text.lower()
        for phrase in self.COMPOUND_PROTECT:
            if phrase in text_lower:
                return [text]

        parts = self.SPLIT_PATTERN.split(text)
        parts = [p.strip() for p in parts if p and p.strip() and len(p.strip()) > 2]

        return parts if parts else [text]

    def handle_input(self, sat_id, text, telemetry=None):
        sub_commands = self._split_compound_input(text)

        if len(sub_commands) <= 1:
            self._handle_normal_command(sat_id, text, telemetry)
        else:
            logger.info(f"🔀 Compound split: {sub_commands}")
            for i, sub in enumerate(sub_commands):
                sub_tele = {"start_time": time.perf_counter()}
                logger.info(f"🔀 Processing sub-command {i+1}/{len(sub_commands)}: '{sub}'")
                is_last = (i == len(sub_commands) - 1)
                self._handle_normal_command(sat_id, sub.strip(), sub_tele, is_silent=(not is_last))

    def _activate_profile(self, sat_id: int, username: str, is_new: bool = False):
        """Consolidated helper to switch users and sync UI."""
        self.state.set_active_user(sat_id, username)
        self.nightwatchman.capture.set_user(username, sat_id)
        
        # Immediate Sync of Vault Files to Database
        user_dir = self.nightwatchman.capture._get_user_dir(sat_id)
        self.nightwatchman._sync_vault_to_sql(user_dir, "Profile Activation")
        
        self._broadcast_memory_matrix(sat_id, username)
        
        msg = f"Welcome back, {username}. I have loaded your profile." if not is_new else f"I've created a new profile for you, {username}. How can I help today?"
        self._speak(sat_id, msg, force_listen=False)

    def _extract_identity_candidate(self, text: str) -> str | None:
        clean_text = re.sub(r"\s+", " ", text.strip())
        patterns = [
            r"^(?:i am|i'm|my name is|call me)\s+([a-zA-Z][a-zA-Z' -]{0,39})$",
            r"^(?:this is)\s+([a-zA-Z][a-zA-Z' -]{0,39})$",
        ]
        for pattern in patterns:
            match = re.match(pattern, clean_text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _looks_like_person_name(self, candidate: str) -> bool:
        if not candidate:
            return False
        normalized = re.sub(r"\s+", " ", candidate.strip())
        if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,39}", normalized):
            return False

        stopwords = {
            "a", "an", "the", "and", "or", "but", "if", "then", "than", "so",
            "hello", "hi", "hey", "thanks", "thank", "sorry", "please",
            "weather", "news", "music", "help", "time", "date", "update",
            "search", "find", "show", "play", "stop", "start", "open", "close",
            "turn", "set", "get", "list", "check", "tell", "explain", "define",
            "describe", "translate", "calculate", "what", "who", "how", "when",
            "where", "why", "which", "today", "tomorrow", "yesterday", "now",
            "price", "cost", "bitcoin", "stock", "market", "data", "movie",
            "movies", "film", "song", "story", "recipe", "fact", "quote",
            "yes", "no", "ok", "okay", "sure", "cancel", "reset", "should",
            "could", "would", "can", "will", "do", "does", "did", "am", "is",
            "are", "was", "were", "be", "being", "been", "shouldnt", "dont",
            "nothing", "something", "anything", "everything", "guest", "profile",
        }
        lowered_tokens = [
            token for token in re.split(r"[ -]+", normalized.lower()) if token
        ]
        if not lowered_tokens or len(lowered_tokens) > 3:
            return False
        if any(token in stopwords for token in lowered_tokens):
            return False
        if all(len(token) == 1 for token in lowered_tokens):
            return False
        return True

    def _canonicalize_profile_name(self, candidate: str) -> str:
        return " ".join(part.capitalize() for part in re.split(r"\s+", candidate.strip()) if part)

    def _is_follow_up_request(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.lower()).strip(" ?.!")
        followups = {
            "more", "more detail", "more details", "details", "tell me more",
            "go on", "continue", "expand", "elaborate", "explain more",
            "what else", "next", "and then", "further details",
        }
        return normalized in followups

    def _infer_weather_kind(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").lower()).strip()
        if "aqi" in normalized or "air quality" in normalized:
            return "aqi"
        if "forecast" in normalized or "tomorrow" in normalized or "next" in normalized:
            return "forecast"
        if "rain" in normalized:
            return "rain"
        return "weather"

    def _looks_like_location_reply(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text.lower()).strip(" ?.!," )
        if not normalized or len(normalized.split()) > 6:
            return False
        if normalized in {"what", "so what is the aqi", "so what is the weather", "weather", "aqi"}:
            return False
        if normalized in {"here", "there"}:
            return False
        if re.fullmatch(r"(?:my|this|current)\s+(?:area|city|town|place|location|region|district|state)", normalized):
            return False
        if re.fullmatch(r"(?:near|around)\s+(?:me|here)", normalized):
            return False
        if re.search(r"\b(?:weather|aqi|forecast|rain|temperature|news|price|stock|bitcoin)\b", normalized):
            return False
        return re.fullmatch(r"[a-zA-Z][a-zA-Z\s.\-]{1,60}", normalized) is not None

    def _apply_pending_weather_followup(self, sat_id: int, text: str) -> str:
        pending_weather = self.state.get_pending_weather(sat_id)
        if not pending_weather:
            return text
        if not self._looks_like_location_reply(text):
            return text
        kind = pending_weather.get("kind", "weather")
        location = text.strip(" ?.!")
        self.state.clear_pending_weather(sat_id)
        self.state.set_last_weather_location(sat_id, location)
        if kind == "aqi":
            return f"what is the AQI in {location}"
        if kind == "forecast":
            return f"weather forecast for {location}"
        if kind == "rain":
            return f"will it rain in {location} today"
        return f"what is the weather in {location}"

    def _apply_weather_location_reuse(self, sat_id: int, text: str) -> str:
        normalized = re.sub(r"\s+", " ", text.lower()).strip(" ?.!," )
        if re.search(r"\bin\s+[a-zA-Z]", normalized):
            return text
        last_location = self.state.get_last_weather_location(sat_id)
        if not last_location:
            return text
        kind = self._infer_weather_kind(normalized)
        if kind == "aqi" and ("aqi" in normalized or "air quality" in normalized):
            return f"what is the AQI in {last_location}"
        if kind == "forecast" and any(term in normalized for term in ["forecast", "tomorrow", "next"]):
            return f"weather forecast for {last_location}"
        if kind == "rain" and "rain" in normalized:
            return f"will it rain in {last_location} today"
        if "weather" in normalized or "temperature" in normalized:
            return f"what is the weather in {last_location}"
        return text

    def _apply_live_followup(self, sat_id: int, text: str) -> str:
        if not self._is_follow_up_request(text):
            return text
        live_ctx = self.state.get_last_live_context(sat_id) or {}
        items = live_ctx.get("items") or []
        if not items:
            return text
        topic = items[0]
        return f"give me more details about {topic}"

    def _capture_live_context(self, sat_id: int, query: str, memory_tank: str):
        if "--- LIVE WEB" not in (memory_tank or ""):
            return
        bullets = []
        for line in memory_tank.splitlines():
            stripped = line.strip()
            if stripped.startswith("• ") and not stripped.startswith("• Source:"):
                item = stripped[2:].strip()
                item = re.sub(r"\s+\([^)]*\)$", "", item).strip()
                if item:
                    bullets.append(item)
        if bullets:
            self.state.set_last_live_context(sat_id, query, bullets)

    def _queue_profile_creation(self, sat_id: int, candidate_name: str):
        self.state.set_pending_identity(sat_id, candidate_name)
        current_user = self.state.get_active_user(sat_id)
        if current_user != "Guest":
            self._speak(
                sat_id,
                f"I don't have a profile for {candidate_name} yet. Should I create one? "
                f"I'll keep {current_user.capitalize()}'s profile loaded until you confirm.",
            )
        else:
            self._speak(sat_id, f"I don't have a profile for {candidate_name} yet. Should I create one for you?")

    # =========================================
    # LOGIC HANDLER — THE BRAIN
    # =========================================
    def _handle_normal_command(self, sat_id, text, telemetry=None, is_silent=False):
        if telemetry is None:
            telemetry = {}
        current_session_id = self.state.session_start_time
        text = self._apply_pending_weather_followup(sat_id, text)
        text = self._apply_weather_location_reuse(sat_id, text)
        text = self._apply_live_followup(sat_id, text)

        # 🟢 IDENTITY LOGIC REFINEMENT (Load vs Create)
        clean_text = text.lower().strip()
        active_user = self.state.get_active_user(sat_id)

        # 1. CREATE INTENT (Explicit)
        create_match = re.search(
            r"(create|make|setup)\s+(?:a\s+)?(?:profile|account)\s+(?:for|of)\s+([a-zA-Z][a-zA-Z' -]{0,39})",
            text,
            flags=re.IGNORECASE,
        )

        # 2. LOAD INTENT (Implicit identification)
        load_name = self._extract_identity_candidate(text)

        # 3. Confirmation State (Wait for Yes/No)
        self.state._init_identity_state(sat_id)
        confirm_state = self.state.identity_state.get(sat_id, {})
        is_waiting_confirm = confirm_state.get("is_waiting_confirm", False)
        pending_name = confirm_state.get("pending_name")
        awaiting_identity = self.state.is_awaiting_identity(sat_id)

        # --- CASE A: Explicit Creation ---
        if create_match:
            raw_name = create_match.group(2).strip()
            if not self._looks_like_person_name(raw_name):
                self._speak(sat_id, "That doesn't sound like a person's name. Tell me the name you want on the profile.")
                return
            new_name = self._canonicalize_profile_name(raw_name)
            self._activate_profile(sat_id, new_name, is_new=True)
            return

        # --- CASE B: Loading Identification ---
        if load_name:
            if not self._looks_like_person_name(load_name):
                self._speak(sat_id, "I need an actual person name before I load or create a profile.")
                return
            id_name = self._canonicalize_profile_name(load_name)
            if self.nightwatchman.capture.user_exists(id_name):
                self._activate_profile(sat_id, id_name, is_new=False)
            else:
                self._queue_profile_creation(sat_id, id_name)
            return

        # --- CASE D: Confirmation Response ---
        if is_waiting_confirm and pending_name:
            if any(w in clean_text for w in ["yes", "yeah", "sure", "ok", "create"]):
                self._activate_profile(sat_id, pending_name, is_new=True)
                self.state.clear_pending_identity(sat_id)
                return
            if any(w in clean_text for w in ["no", "dont", "don't", "stop"]):
                fallback_user = self.state.get_active_user(sat_id)
                if fallback_user != "Guest":
                    self._speak(sat_id, f"No problem. I'll keep {fallback_user.capitalize()}'s profile loaded.")
                else:
                    self._speak(sat_id, "No problem. I'll keep you as Guest for now.")
                self.state.clear_pending_identity(sat_id)
                return
            self.state.clear_pending_identity(sat_id)

        # 🟢 UNIFIED INTENT PROCESSING: Rely on PiManager
        plan = self.pi.process_query(sat_id, text)

        # Reflex Execution (Bypasses Identity Tracking)
        if plan:
            plan["raw_input"] = text
            intent = plan.get("intent")
            if intent == "RESUME_CONFIRMED":
                self.handle_resume_confirmation(sat_id, True); return
            if intent == "RESUME_CANCELLED":
                self.handle_resume_confirmation(sat_id, False); return
            if intent and intent != "unknown":
                # Clear identity prompt if we executed a command instead of answering the name
                if awaiting_identity:
                    self.state.clear_identity_prompt(sat_id)
                self._execute_plan(sat_id, plan, telemetry); return

        # --- CASE C: Prompted Response (Simplified "Nishchay") ---
        if awaiting_identity and len(clean_text.split()) <= 3 and not self._is_follow_up_request(clean_text):
            candidate = re.sub(r"\b(?:here|this is|it's|its)\b", "", clean_text).strip()
            candidate = " ".join(candidate.split()[:3])
            if not self._looks_like_person_name(candidate):
                self.state.clear_identity_prompt(sat_id)
                # Fall through to normal pipeline instead of treating as name
            else:
                name = self._canonicalize_profile_name(candidate)
                if self.nightwatchman.capture.user_exists(name):
                    self._activate_profile(sat_id, name, is_new=False)
                else:
                    self._queue_profile_creation(sat_id, name)
                return
        elif awaiting_identity:
            self.state.clear_identity_prompt(sat_id)

        # --- NEW DECISION TREE (The "Doors") ---
        # Door 1: Reflex Catalog (Handled above by pi.process_query)
        
        # We need the vector for both Hot Cache and standard pipeline
        q_vec = self._get_nomic_vector(text)
        
        if q_vec is not None:
            # Door 2: Hot Cache (Idle Librarian Data)
            hot_match = self.db.get_hot_cache_match(q_vec, threshold=0.70)
            if hot_match:
                logger.info(f"⚡ FAST PATH: Hot Cache matched '{text}' < 50ms")
                self._speak(sat_id, hot_match, force_listen=False, telemetry=telemetry)
                return
            
        # Door 3: API Scout — handled inside router's web route (enrich_packet)
        # When router decides "web", it checks APIScout first → if no match → SearxNG/DDG
                
        # Door 4: Deep Web / General Routing (Proceeds Below)

        is_fresh_session       = not getattr(self.state, 'is_conversation', False)
        self.state.is_conversation = True

        router_start           = time.perf_counter()
        current_topic, confidence = self.librarian.get_topic_with_score(text)
        telemetry["router_lat_ms"] = round(
            (time.perf_counter() - router_start) * 1000, 2)

        last_topic  = self.state.cognitive.get(sat_id, {}).get("topic", "general")
        play_filler = (current_topic != last_topic) or is_fresh_session

        if sat_id in self.state.cognitive:
            self.state.cognitive[sat_id]["topic"] = current_topic

        self.emit_to_webui(sat_id, "engine_state", "processing")
        
        if str(sat_id) != "0":
            self.emitter.emit("start_thinking_audio", sat_id, {
                "topic": current_topic, "play_filler": play_filler
            })

        self.is_thinking = True
        llm_start = time.perf_counter()

        # 🟢 Retrieve active user FIRST, so DB and NightWatchman can use it
        active_user = self.state.get_active_user(sat_id)
        self.api_scout.current_user = active_user

        sql_memory = ""
        if q_vec is not None:
            relevant = self.db.get_relevant_memories(active_user, q_vec, top_k=3)
            sql_memory = "\n".join(relevant) if relevant else ""

        # Point the working memory capture to the correct user vault
        self.nightwatchman.capture.set_user(active_user, sat_id)

        self.state.update_context(sat_id, text, new_entities={})

        recent_queries = [
            turn.get("content", "")
            for turn in self.state.get_recent_context(sat_id)
            if turn.get("role") == "user" and turn.get("content")
        ]

        # 🟢 CONSOLIDATED CONTEXT: build against the active session timeline
        memory_ctx = self.assembler.build_context_string(
            active_user,
            text,
            recent_queries=recent_queries,
        )

        if is_silent:
            logger.info(f"🔕 Silent chunk saved to history: '{text}'")
            return

        # 🟢 Create the Golden Packet
        packet = self.state.build_golden_packet(
            sat_id,
            text,
            "neutral",
            memory_context=memory_ctx
        )


        packet = self.librarian.enrich_packet(packet)
        resolved_input = packet.get('input', text)

        if packet.get('route_taken') == "general_web_search" and not packet.get('needs_memory'):
            packet['memory_context'] = ""

        if packet.get('web_data'):
            packet['memory_tank'] = packet['web_data']
        elif sql_memory:
            packet['memory_tank'] = sql_memory
        elif not packet.get('memory_tank'):
            packet['memory_tank'] = ""

        print("\n" + "=" * 60)
        print("🔍 DEBUG: GOLDEN PACKET")
        print("-" * 60)
        print(f"ACTIVE USER: {active_user}")
        print(f"ROUTE:       {packet.get('route_taken')}")
        print(f"MEMORY_CTX:\n{memory_ctx or '(empty)'}")
        print(f"MEMORY_TANK:\n{packet.get('memory_tank') or '(empty)'}")
        print(f"HISTORY:\n{packet.get('history')}")
        print("=" * 60 + "\n")

        try:
            self._capture_live_context(sat_id, resolved_input, packet.get("memory_tank") or "")
            direct_weather_location = self._direct_weather_location_reply(packet)
            if direct_weather_location:
                self.state.set_pending_weather(sat_id, self._infer_weather_kind(resolved_input))
                llm_response_dict = {
                    "response": direct_weather_location,
                    "active_subject": "weather_location_required",
                    "is_action": False,
                    "execute": None,
                }
            else:
                direct_weather_unavailable = self._direct_weather_unavailable_reply(packet)
                if direct_weather_unavailable:
                    self.state.set_pending_weather(sat_id, self._infer_weather_kind(resolved_input))
                    llm_response_dict = {
                        "response": direct_weather_unavailable,
                        "active_subject": "weather_unavailable",
                        "is_action": False,
                        "execute": None,
                    }
                else:
                    direct_live_failure = self._direct_live_failure_reply(resolved_input, packet)
                    if direct_live_failure:
                        llm_response_dict = {
                            "response": direct_live_failure,
                            "active_subject": "live_lookup_failed",
                            "is_action": False,
                            "execute": None,
                        }
                    else:
                        direct_live_reply = self._direct_counted_live_reply(resolved_input, packet)
                        if direct_live_reply:
                            llm_response_dict = {
                                "response": direct_live_reply,
                                "active_subject": "live_list",
                                "is_action": False,
                                "execute": None,
                            }
                        else:
                            llm_response_dict = self.llm.generate(packet)

            if self.state.session_start_time != current_session_id:
                logger.warning("🚫 Barge-in detected. Aborting stale response.")
                return

            if not llm_response_dict or not isinstance(llm_response_dict, dict):
                logger.error("⚠️ LLM returned invalid response.")
                self._speak(sat_id, "I'm sorry, I lost my train of thought.")
                return

            llm_response   = llm_response_dict.get(
                "response", "I'm not sure how to answer that.")
            active_subject = llm_response_dict.get("active_subject", "general")

            # 🟢 FIX 3: The Deterministic Identity Nag
            if packet.get("needs_identity_prompt") and active_user == "Guest":
                logger.info(f"👤 Appending identity prompt deterministically for Guest [Sat {sat_id}].")
                llm_response += " By the way, I don't believe we've been properly introduced. What should I call you?"
                self.state.mark_identity_prompted(sat_id)

            telemetry["llm_lat_ms"] = round(
                (time.perf_counter() - llm_start) * 1000, 2)

            self.state.commit_assistant_response(sat_id, llm_response, active_subject)

            interaction_id = self.db.log_event(
                resolved_query    = resolved_input,
                topic_category    = current_topic,
                nomic_confidence  = confidence,
                extracted_entities = {}
            )

            # 🟢 TRIGGER MEMORY CAPTURE (Background Triage + Queue Task)
            self.nightwatchman.capture.capture(sat_id, resolved_input, llm_response, interaction_id=interaction_id)

            if active_user == "Guest":
                logger.info("预览: Guest profile active. Deep memory extraction suppressed in background thread.")

            if llm_response_dict.get("is_action"):
                self._execute_plan(sat_id, {
                    "intent":    "LLM_ACTION",
                    "execute":   llm_response_dict.get("execute"),
                    "speak":     llm_response,
                    "raw_input": text,
                }, telemetry=telemetry, skip_log=False)
            else:
                self._speak(sat_id, llm_response,
                            force_listen=True, telemetry=telemetry)

        except Exception as e:
            logger.error(f"❌ LLM Pipeline Error: {e}")
            if self.state.session_start_time == current_session_id:
                self._speak(sat_id, "I'm having trouble connecting to my brain.")
        finally:
            self.is_thinking = False

    # =========================================
    # RESUME
    # =========================================
    def handle_resume_confirmation(self, sat_id, confirmed=True):
        if not confirmed:
            self.state.reset_interruption(sat_id)
            self._speak(sat_id, "Okay, stopping.")
            return
        interrupted = self.state.interrupted_state.get(sat_id)
        if not interrupted:
            return
        original_file = interrupted.get("file")
        if original_file and os.path.exists(original_file):
            time.sleep(1.0)
            self._speak_file(sat_id, original_file, force_listen=True)
            self.state.reset_interruption(sat_id)
        else:
            self.state.reset_interruption(sat_id)

    # =========================================
    # SUDO
    # =========================================
    def _handle_sudo_command(self, sat_id, text):
        clean = text.lower().strip()
        if "exit" in clean:
            self.security_mode = "NORMAL"
            self._speak(sat_id, "Exiting Sudo Mode.")
            return
        if self.pending_sudo_cmd:
            if clean == self.pending_sudo_cmd:
                self._speak(sat_id, "Executing.")
                self._execute_sudo_action(sat_id, self.pending_sudo_cmd)
                self.pending_sudo_cmd = None
            else:
                self._speak(sat_id, "Mismatch. Command cancelled.")
                self.pending_sudo_cmd = None
            return
        if "reboot" in clean:
            self.pending_sudo_cmd = "reboot"
            self._speak(sat_id, "Confirm Reboot.")
        else:
            self._speak(sat_id, "Unknown Sudo Command.")

    def _enter_sudo_calibration(self, sat_id):
        self._speak(sat_id, "Checking environment. Please be silent.")
        time.sleep(2)
        if self.comms:
            self.comms.send_command(sat_id, {"cmd": "calibrate"})
        time.sleep(4)
        self.security_mode = "SUDO_SESSION"
        self._speak(sat_id, "Environment Safe. Root Access Granted.")

    def _execute_sudo_action(self, sat_id, action):
        if action == "reboot":
            os.system("sudo reboot")

    # =========================================
    # PLAN EXECUTION
    # =========================================
    def _execute_plan(self, sat_id, plan, telemetry=None, skip_log=False):
        execute_payload = plan.get("execute")
        base_speech     = plan.get("speak")
        target_device   = "system"
        action_name     = "unknown_action"

        if execute_payload and isinstance(execute_payload, dict):
            if "action" in execute_payload:
                action_name = execute_payload["action"]
                if action_name == "fetch_time":
                    base_speech = f"It is {datetime.now().strftime('%I:%M %p').lstrip('0')}."
                elif action_name == "fetch_date":
                    base_speech = f"Today is {datetime.now().strftime('%A, %B %d, %Y')}."
                elif action_name == "stop_audio":
                    self._close_session(sat_id); return
                elif action_name == "list_apis":
                    self._handle_list_apis(execute_payload, sat_id, current_text=plan.get("raw_input")); return
            elif "domain" in execute_payload and "service" in execute_payload:
                domain        = execute_payload["domain"]
                service       = execute_payload["service"]
                target_device = execute_payload.get("entity_id", f"{domain}_entity")
                action_name   = f"{domain}.{service}"
                self.ha.execute(action_name)

        force_listen = plan.get("force_listen", False)
        has_action   = bool(execute_payload)

        if not skip_log and has_action:
            tid = self.db.log_reflex_start(
                sat_id=sat_id, target_device=target_device,
                action_executed=action_name)
            if tid:
                self.db.log_reflex_end(tid)

        if self.state.resume_pending.get(sat_id):
            if has_action:
                subj = self.state.cognitive.get(sat_id, {}).get("active_subject", "general")
                rp   = (f"Would you like me to finish what I was saying about {subj}?"
                        if subj and subj != "general"
                        else "Would you like me to finish what I was saying?")
                self._speak(sat_id, f"{base_speech}. {rp}" if base_speech else rp,
                            force_listen=True, telemetry=telemetry)
            else:
                if base_speech:
                    self._speak(sat_id, base_speech,
                                force_listen=True, telemetry=telemetry)
        else:
            if base_speech:
                self._speak(sat_id, base_speech, force_listen, telemetry=telemetry)
            elif force_listen and self.comms:
                self.comms.send_command(sat_id, {"cmd": "force_listen", "timeout": 4})

    # =========================================
    # SPEECH OUTPUT
    # =========================================
    def _speak(self, sat_id, text, force_listen=False, telemetry=None):
        self.emit_to_webui(sat_id, "syntheta_response", text)
        
        if str(sat_id) == "0":
            logger.info(f"🌐 [WebUI Virtual] SYNTHETA SAYS: {text}")
            return 
            
        if self.tts:
            self._speak_direct(sat_id, text, force_listen, telemetry)
        else:
            print(f"\n💬 SYNTHETA SAYS: {text}\n")

    def _speak_direct(self, sat_id, text, force_listen=False, telemetry=None):
        tts_start = time.perf_counter()
        path = self.tts.generate_to_file(text)
        if telemetry is not None:
            telemetry["tts_lat_ms"] = round(
                (time.perf_counter() - tts_start) * 1000, 2)
            self._report_latency(telemetry)
        self._speak_file(sat_id, path, force_listen)

    def _speak_file(self, sat_id, path, force_listen=False):
        if not path or not os.path.exists(path):
            return
        duration    = self.state.get_wav_duration(path)
        silent_zone = duration + 2.0

        if self.comms:
            self.comms.send_command(sat_id, {"cmd": "wwd_mode", "value": "silent"})

        if sat_id in self.wwd_timers:
            self.wwd_timers[sat_id].cancel()

        if not force_listen:
            self.wwd_timers[sat_id] = threading.Timer(
                silent_zone,
                lambda: self.comms.send_command(
                    sat_id, {"cmd": "wwd_mode", "value": "normal"})
            )
            self.wwd_timers[sat_id].start()

        self.state.track_playback(sat_id, path)
        self.state.deaf_until = time.time() + duration + 0.2
        self.pending_force_listen[sat_id] = force_listen
        self.emitter.emit("play_file", sat_id, {"filepath": path})

    def _handle_list_apis(self, payload, sat_id, current_text=""):
        """Fetches and formats a list of working APIs for a category."""
        target_cat = payload.get("category")
        if target_cat == "context_aware" and current_text:
            # Try to extract category from text (e.g. "animal" from "list animal apis")
            target_cat = self.api_scout.match_bucket_to_category(current_text)
            
        if not target_cat:
            self._speak(sat_id, "I couldn't identify which category of APIs you're looking for. Could you be more specific?")
            return

        apis = self.api_scout.get_working_apis(target_cat, count=3)
        if not apis:
            self._speak(sat_id, f"I found the '{target_cat}' category, but none of the APIs are currently responding. I can try a web search instead if you'd like.")
            return

        reply = f"Here are some working APIs for {target_cat}:\n"
        for i, api in enumerate(apis, 1):
            reply += f"{i}. {api['name']}: {api['url']}\n"
        
        self._speak(sat_id, reply, force_listen=False)

    # =========================================
    # LATENCY REPORT
    # =========================================
    def _report_latency(self, tele):
        if not ENABLE_LATENCY_TELEMETRY:
            return
        start = tele.get("start_time", time.perf_counter())
        total = round((time.perf_counter() - start) * 1000, 2)
        print("\n" + "=" * 45)
        print(f"📊 OMEGA PERFORMANCE REPORT (Total: {total}ms)")
        print("-" * 45)
        print(f" 🟢 STT (Whisper):   {tele.get('stt_lat_ms',    0):>7} ms")
        print(f" 🔵 Brain (NLU):     {tele.get('brain_lat_ms',  0):>7} ms")
        print(f" 🧠 Router:          {tele.get('router_lat_ms', 0):>7} ms")
        print(f" 📚 RAG:             {tele.get('rag_lat_ms',    0):>7} ms")
        print(f" 🟣 LLM:             {tele.get('llm_lat_ms',   0):>7} ms")
        print(f" 🟠 TTS (Kokoro):    {tele.get('tts_lat_ms',   0):>7} ms")
        print("=" * 45 + "\n")
