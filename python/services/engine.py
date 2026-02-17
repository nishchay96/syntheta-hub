import threading
import time
import numpy as np
import logging
import sys
import os
import re 
import json
import wave
import random

# ============================================
# 🔧 SERVICE IMPORTS (ROBUST PATHING)
# ============================================
current_dir = os.path.dirname(os.path.abspath(__file__))
python_root = os.path.abspath(os.path.join(current_dir, '..'))
hub_root = os.path.abspath(os.path.join(python_root, '..'))
audio_lib_dir = os.path.join(python_root, 'audio')

if audio_lib_dir not in sys.path:
    sys.path.insert(0, audio_lib_dir)
if python_root not in sys.path:
    sys.path.insert(0, python_root)
if hub_root not in sys.path:
    sys.path.insert(0, hub_root)

try:
    from stt_event_emitter import STTEventEmitter
except ImportError:
    try:
        from python.audio.stt_event_emitter import STTEventEmitter
    except ImportError:
        from audio.stt_event_emitter import STTEventEmitter

from core.gatekeeper import AudioGatekeeper
from nlu.llm_bridge import OllamaBridge
from nlu.semantic_brain import SemanticBrain 
from core.pi_manager import PiManager
from .state_manager import EngineState
from .smart_tts_cache import SmartTTSCache 
from .transcriber import AudioTranscriber
# 🟢 FIX: Explicitly import telemetry flag
from .config import *
from .config import ENABLE_LATENCY_TELEMETRY

from .communications import HomeAssistantClient
from .audio_tools import create_resume_file, pad_audio_file
from tts_engine import TTSEngine

logger = logging.getLogger("SynthetaEngine")

# ============================================
# 🔧 TUNING PARAMETERS
# ============================================
LIMIT_REFLEX = 40.0        
LIMIT_CONVERSATION = 120.0 
SESSION_IDLE_TIMEOUT = 10.0
WAKE_COLLISION_SKIP_BYTES = 16000 
PCM_BYTES_PER_SEC = 32000
UDP_PAYLOAD_SIZE = 1024 
GHOST_STREAM_LIMIT_SEC = 5.0 
PACKETS_PER_SEC = PCM_BYTES_PER_SEC / UDP_PAYLOAD_SIZE

DEFAULT_HALLUCINATIONS = [
    "you", "thank you", "thanks", "start", "stop", "no", "yes",
    "subtitles", "copyright", "audio", "video", "subscribe", 
    "watching", "bye", "amara", "org"
]

class CommsShim:
    def __init__(self, engine): self.engine = engine
    def emit(self, event, sat_id, payload):
        if self.engine.comms:
            pass 

# ============================================
# 🧠 MAIN ENGINE (OMEGA V2.5 - COGNITIVE)
# ============================================
class SynthetaEngine:
    def __init__(self, state_manager, pi_manager):
        logger.info("⚡ Initializing Syntheta Engine (Omega v2.5 - Cognitive)...")
        
        self.state = state_manager
        self.pi = pi_manager
        self.comms = None 
        
        self.ha = HomeAssistantClient(HA_TOKEN, HA_URL)
        self.emitter = STTEventEmitter()
        
        # 🟢 UPGRADED: Offloading to VRAM via ASR_DEVICE if configured
        self.transcriber = AudioTranscriber()
        self.brain = SemanticBrain() 
        self.llm = OllamaBridge()
        self.gatekeeper = AudioGatekeeper()
        
        try: 
            # 🟢 FIX: TTS initialized with CUDA support from config
            self.tts = TTSEngine()
            self.smart_cache = SmartTTSCache(self.tts, CommsShim(self))
            logger.info("✅ TTS & Smart Cache Online (VRAM Ready)")
        except Exception as e: 
            logger.warning(f"⚠️ TTS Disabled: {e}")
            self.tts = None
            self.smart_cache = None

        self.security_mode = "NORMAL" 
        self.sudo_timer = 0
        self.sudo_challenge_deadline = 0
        self.pending_sudo_cmd = None
        self.hallucinations = list(DEFAULT_HALLUCINATIONS)
        self.ghost_counters = {} 

        self.wwd_timers = {} 
        
        threading.Thread(target=self._processing_loop, daemon=True).start()
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        threading.Thread(target=self._sudo_heartbeat_loop, daemon=True).start()
        
        self._play_boot_sound()
        logger.info("🟢 ENGINE READY.")

    def register_comms(self, comms_instance):
        self.comms = comms_instance
        logger.info("🔗 Network Manager Registered with Engine.")

    def _play_boot_sound(self):
        boot_wav = os.path.join(os.path.dirname(__file__), '../../assets/system/boot.wav')
        if os.path.exists(boot_wav):
            self._speak_file(1, boot_wav)

    # =========================================
    #  EVENT HANDLERS
    # =========================================
    def on_hardware_wake(self, sat_id, payload=None):
        logger.info(f">>> ⚡ HARDWARE WAKE: Satellite {sat_id}")

        if sat_id in self.wwd_timers:
            self.wwd_timers[sat_id].cancel()
            del self.wwd_timers[sat_id]
            logger.info(f"🚫 Canceled Un-mute timer for Sat {sat_id} due to interruption.")

        self.state.snapshot_playback(sat_id)
        self.state.session_origins[sat_id] = "barge_in"
        self.ghost_counters[sat_id] = 0
        
        with self.state.audio_queue.mutex:
             self.state.audio_queue.queue.clear()
        
        self.state.deaf_until = 0.0
        self.state.skip_byte_counter = WAKE_COLLISION_SKIP_BYTES
        self.state.audio_buffers[sat_id] = b""
        self.state.session_start_time = time.time()
        self.state.is_conversation = False
        self.state.last_active_time[sat_id] = time.time()
        self.state.session_mode[sat_id] = "LISTENING"
        
        self.pi.start_new_session(sat_id)

    def on_calibration_update(self, sat_id, floor):
        self.gatekeeper.update_calibration(sat_id, floor)

    def queue_audio(self, sat_id, pcm):
        self.state.last_active_time[sat_id] = time.time()
        if self.state.session_mode.get(sat_id) == "LISTENING":
            try: 
                self.state.audio_queue.put_nowait((sat_id, pcm))
            except Exception as e:
                logger.error(f"❌ Queue Full/Error: {e}")

    def flush_audio(self, sat_id):
        logger.info(f"🚀 Hardware Trigger: Flushing Buffer for Sat {sat_id}")
        self._transcribe(sat_id)

    # =========================================
    #  CORE LOOPS
    # =========================================
    def _processing_loop(self):
        logger.info("✅ Processing Loop Started")
        while True:
            try:
                sat_id, pcm = self.state.audio_queue.get()
                self._process_audio_chunk(sat_id, pcm)
                self.state.audio_queue.task_done()
            except Exception as e:
                logger.error(f"❌ CRITICAL PROCESS LOOP CRASH: {e}", exc_info=True)
                time.sleep(0.1)

    def _monitor_loop(self):
        while True:
            time.sleep(0.5)
            now = time.time()
            if self.security_mode == "SUDO_CHALLENGE":
                if now > self.sudo_challenge_deadline:
                    logger.info("🚫 Sudo Challenge Expired.")
                    self.security_mode = "NORMAL"
                    self._speak(1, "Login timeout.")
            for sat_id, mode in list(self.state.session_mode.items()):
                if mode != "LISTENING": continue
                if self.security_mode == "SUDO_SESSION": continue
                limit = LIMIT_CONVERSATION if getattr(self.state, 'is_conversation', False) else LIMIT_REFLEX
                start_time = getattr(self.state, 'session_start_time', now)
                if (now - start_time) > limit:
                      self._close_session(sat_id)
                      continue
                last_active = self.state.last_active_time.get(sat_id, now)
                if (now - last_active) > SESSION_IDLE_TIMEOUT:
                    self._close_session(sat_id)

    def _sudo_heartbeat_loop(self):
        while True:
            if self.security_mode == "SUDO_SESSION":
                if self.comms: self.comms.send_keep_alive(1)
            time.sleep(25)

    def _close_session(self, sat_id):
        self.state.session_mode[sat_id] = "IDLE"
        self.state.deaf_until = 0.0

    # =========================================
    #  AUDIO PIPELINE (SMART FILTERING)
    # =========================================
    def _process_audio_chunk(self, sat_id, pcm):
        if time.time() < self.state.deaf_until: return
        if hasattr(self.state, 'skip_byte_counter') and self.state.skip_byte_counter > 0:
            skip_amount = min(len(pcm), self.state.skip_byte_counter)
            self.state.skip_byte_counter -= skip_amount
            if skip_amount == len(pcm): return
            pcm = pcm[skip_amount:]
        if sat_id not in self.state.audio_buffers:
            self.state.audio_buffers[sat_id] = b""
        self.state.audio_buffers[sat_id] += pcm

    def _transcribe(self, sat_id):
        audio_data = self.state.audio_buffers.get(sat_id, b"")[:]
        self.state.audio_buffers[sat_id] = b""
        if len(audio_data) < 3200:
            logger.warning(f"⚠️ Buffer too short to transcribe ({len(audio_data)} bytes)")
            return
        if not self.gatekeeper.is_speech(sat_id, audio_data):
            logger.warning(f"🛡️ False Wake Rejected [Sat {sat_id}]. Audio below calibrated threshold.")
            self._close_session(sat_id)
            return
        threading.Thread(target=self._run_pipeline, args=(sat_id, audio_data)).start()

    def _run_pipeline(self, sat_id, audio_bytes):
        text, confidence, turn_telemetry = self.transcriber.transcribe(audio_bytes)
        turn_telemetry["start_time"] = time.perf_counter()
        
        if not text or len(text) < 2 or confidence < 0.4: return
        if text.lower() in self.hallucinations: return
        
        logger.info(f">>> 📝 INPUT: '{text}' (Conf: {confidence:.2f}) [Mode: {self.security_mode}]")
        self.state.last_active_time[sat_id] = time.time()
        
        if self.security_mode == "SUDO_CHALLENGE":
            if "sudo login" in text.lower():
                self._enter_sudo_calibration(sat_id)
            else:
                logger.info("🔒 Ignored input during Sudo Challenge.")
            return
        if self.security_mode == "SUDO_SESSION":
            self._handle_sudo_command(sat_id, text)
            return
            
        self._handle_normal_command(sat_id, text, turn_telemetry)

    # =========================================
    #  LOGIC HANDLERS (THE BRAIN)
    # =========================================
    def _handle_normal_command(self, sat_id, text, telemetry=None):
        if telemetry is None: telemetry = {}
        
        # 🟢 LAYER 1: FAST REFLEX (High-Confidence Context Bypass)
        brain_start = time.perf_counter()
        plan = self.pi.process_query(sat_id, text)
        
        # 🟢 FIX: Allow high-confidence commands to bypass the resume trap
        if self.state.resume_pending.get(sat_id):
             score = plan.get("confidence", 0) if plan else 0
             if score > 0.85:
                  logger.info(f"⚡ Context Bypass: High-Confidence command '{text}' preempts resume.")
                  self.state.resume_pending[sat_id] = False
             else:
                  # Handle standard resume confirmation
                  self.state.resume_pending[sat_id] = False 
                  clean_input = text.lower()
                  if "yes" in clean_input or "continue" in clean_input:
                       self.handle_resume_confirmation(sat_id, True)
                       return
                  elif "no" in clean_input or "stop" in clean_input:
                       self.handle_resume_confirmation(sat_id, False)
                       return
                  else:
                       logger.warning(f"❓ Ambiguous response to confirmation: {text}")
                       self.state.resume_pending[sat_id] = True

        if plan and plan.get("intent") == "SUDO_ACCESS":
            self.security_mode = "SUDO_CHALLENGE"
            self.sudo_challenge_deadline = time.time() + 15.0
            self._speak(sat_id, "Did you mean Sudo Access? Say Sudo Login to confirm.")
            return
            
        # 🟢 FIX: Prevent internal logic intents from reaching HA execute
        if plan and plan.get("intent") != "unknown":
            telemetry["brain_lat_ms"] = round((time.perf_counter() - brain_start) * 1000, 2)
            # Internal logic intents like 'CONFIRM_YES' are handled here, not sent to HA
            if plan.get("intent") not in ["CONFIRM_YES", "CONFIRM_NO"]:
                self._execute_plan(sat_id, plan, telemetry)
            else:
                logger.info(f"🧠 Internal Intent '{plan['intent']}' processed successfully.")
            return
            
        self.state.is_conversation = True
        processed = self.brain.process(text)
        telemetry["brain_lat_ms"] = round((time.perf_counter() - brain_start) * 1000, 2)
        
        # LLM Pipeline
        llm_start = time.perf_counter()
        self.state.update_context(sat_id, processed['input'], processed['entities'])
        packet = self.state.build_golden_packet(sat_id, processed['input'], processed.get('emotion', 'neutral'))
        llm_response = self.llm.generate(packet)
        telemetry["llm_lat_ms"] = round((time.perf_counter() - llm_start) * 1000, 2)
        
        self.state.commit_assistant_response(sat_id, llm_response)
        self._speak(sat_id, llm_response, telemetry=telemetry)

    def handle_resume_confirmation(self, sat_id, confirmed=True):
        if not confirmed:
            self.state.reset_interruption(sat_id)
            self._speak(sat_id, "Okay, stopping.")
            return

        interrupted = self.state.interrupted_state.get(sat_id)
        if not interrupted: return

        resume_file_path = create_resume_file(
            interrupted["file"], 
            interrupted.get("seconds_played", 0), 
            rewind_sec=2.0
        )

        if resume_file_path:
            self._speak_file(sat_id, resume_file_path)
            self.state.reset_interruption(sat_id)

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
        elif "shutdown" in clean:
            self.pending_sudo_cmd = "shutdown"
            self._speak(sat_id, "Confirm Shutdown.")
        elif "update" in clean:
            self.pending_sudo_cmd = "update"
            self._speak(sat_id, "Confirm Force Update.")
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
        elif action == "shutdown":
            os.system("sudo shutdown now")
        elif action == "update":
            self._speak(sat_id, "Starting update sequence...")
    
    # =========================================
    #  EXECUTION & OUTPUT
    # =========================================
    def _execute_plan(self, sat_id, plan, telemetry=None):
        if plan.get("intent") == "STOP":
             self._close_session(sat_id)
             return
        if plan.get("execute"):
            # 🟢 FIX: Ensure we only send valid HA strings to HA client
            if isinstance(plan["execute"], str) and "." in plan["execute"]:
                self.ha.execute(plan["execute"])
        force_listen = plan.get("force_listen", False)
        if plan.get("speak"):
            self._speak(sat_id, plan["speak"], force_listen, telemetry=telemetry)
        elif force_listen and self.comms:
            self.comms.send_command(sat_id, {"cmd": "force_listen", "timeout": 4})

    def _speak(self, sat_id, text, force_listen=False, telemetry=None):
        if self.tts:
            if self.smart_cache:
                self.smart_cache.process_and_speak(sat_id, text, lambda t: self._speak_direct(sat_id, t, force_listen, telemetry))
            else:
                self._speak_direct(sat_id, text, force_listen, telemetry)

    def _speak_direct(self, sat_id, text, force_listen=False, telemetry=None):
        tts_start = time.perf_counter()
        path = self.tts.generate_to_file(text)
        
        if telemetry is not None:
            telemetry["tts_lat_ms"] = round((time.perf_counter() - tts_start) * 1000, 2)
            self._report_latency(telemetry)
            
        self._speak_file(sat_id, path, force_listen)

    def _speak_file(self, sat_id, path, force_listen=False):
        if not path or not os.path.exists(path): return
        
        duration = self.state.get_wav_duration(path)
        silent_zone = duration + 2.0

        if self.comms:
            self.comms.send_command(sat_id, {"cmd": "wwd_mode", "value": "silent"})

        if sat_id in self.wwd_timers: self.wwd_timers[sat_id].cancel()
        self.wwd_timers[sat_id] = threading.Timer(silent_zone, 
            lambda: self.comms.send_command(sat_id, {"cmd": "wwd_mode", "value": "normal"}))
        self.wwd_timers[sat_id].start()

        self.state.track_playback(sat_id, path)
        self.state.deaf_until = time.time() + duration + 0.2
        logger.info(f"🔈 Handing off playback to Go Bridge for Sat {sat_id}")
        self.emitter.emit("play_file", sat_id, {"filepath": path})
        
        if force_listen and self.comms:
            logger.info(f"🎤 Scheduling Remote Mic Open (force_listen) for Sat {sat_id} in {duration:.2f}s")
            threading.Timer(duration + 0.2, lambda: self.comms.send_command(sat_id, {"cmd": "force_listen", "timeout": 4})).start()

    def _report_latency(self, tele):
            if not ENABLE_LATENCY_TELEMETRY: return
            
            stt = tele.get("stt_lat_ms", 0)
            brain = tele.get("brain_lat_ms", 0)
            llm = tele.get("llm_lat_ms", 0)
            tts = tele.get("tts_lat_ms", 0)
            
            start = tele.get("start_time", time.perf_counter())
            total = round((time.perf_counter() - start) * 1000, 2)
            
            print("\n" + "="*45)
            print(f"📊 OMEGA PERFORMANCE REPORT (Total: {total}ms)")
            print("-" * 45)
            print(f" 🟢 STT (Whisper):   {stt:>7} ms")
            print(f" 🔵 Brain (NLU):   {brain:>7} ms")
            print(f" 🟣 LLM (Ollama):  {llm:>7} ms")
            print(f" 🟠 TTS (Kokoro):  {tts:>7} ms")
            print("="*45 + "\n")