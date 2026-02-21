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

from core.knowledge_manager import KnowledgeManager
from core.gatekeeper import AudioGatekeeper
from nlu.llm_bridge import OllamaBridge
from nlu.semantic_brain import SemanticBrain 
from core.pi_manager import PiManager
from .state_manager import EngineState
from .transcriber import AudioTranscriber

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
        
        self.transcriber = AudioTranscriber()
        self.brain = SemanticBrain() 
        self.llm = OllamaBridge()
        self.gatekeeper = AudioGatekeeper()
        
        try: 
            # 🟢 PURE TTS: Direct engine without caching layer
            self.tts = TTSEngine()
            logger.info("✅ TTS Engine Online (VRAM Optimized)")
        except Exception as e: 
            logger.warning(f"⚠️ TTS Disabled: {e}")
            self.tts = None
        
                # 🟢 NEW: Initialize Knowledge Manager (RAG with BGE-M3 + Reranker)
        try:
            self.knowledge = KnowledgeManager()
            logger.info("✅ OMEGA Knowledge Engine Online (BGE-M3 + Reranker)")
        except Exception as e:
            logger.error(f"❌ Knowledge Engine failed: {e}")
            self.knowledge = None


        self.security_mode = "NORMAL" 
        self.sudo_timer = 0
        self.sudo_challenge_deadline = 0
        self.pending_sudo_cmd = None
        self.hallucinations = list(DEFAULT_HALLUCINATIONS)
        self.ghost_counters = {} 

        self.wwd_timers = {}
        self.pending_force_listen = {} # 🟢 NEW: Tracks deterministic mic triggers 
        
        # 🟢 State flag for Music Bridge logic
        self.is_thinking = False
        
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

        self.emitter.emit("stop_audio", sat_id, {})

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
        self.state.clear_playback(sat_id)
        
    def on_playback_finished(self, sat_id, filename):
        base_file = os.path.basename(filename)
        logger.info(f"🎵 Playback Finished [Sat {sat_id}]: {base_file}")
        
        if "satellite_connect" in base_file:
            logger.info(f"📢 Calibration Warning Finished. Waiting for room silence...")
            time.sleep(1.5) 
            
            if self.comms:
                logger.info(f"⚙️ Sending Calibration Command to Sat {sat_id}")
                self.comms.send_command(sat_id, {"cmd": "calibrate"})
            return

        # 🟢 MUSIC BRIDGE: Triggers if filler ends while LLM is still 'thinking'
        if "filler_" in base_file and self.is_thinking:
            logger.info(f"⏳ Filler finished but LLM is thinking. Bridging with music.")
            self.emitter.emit("play_music", sat_id, {"filename": "bridge.wav"})

        # 🟢 DETERMINISTIC MIC TRIGGER
        if self.pending_force_listen.get(sat_id):
            logger.info(f"🎤 Playback complete. Firing natural mic open (force_listen) for Sat {sat_id}")
            if self.comms:
                self.comms.send_command(sat_id, {"cmd": "force_listen", "timeout": 4})
            self.pending_force_listen[sat_id] = False
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
        
        # 🟢 STEP 1: Cognitive Pre-Processing
        brain_start = time.perf_counter()
        processed = self.brain.process(text)
        topic = processed.get('topic')
        telemetry["brain_lat_ms"] = round((time.perf_counter() - brain_start) * 1000, 2)

        # 🟢 STEP 2: SEMANTIC ROUTING (The Traffic Cop)
        context = ""
        
        # Route A: The Lore Book (0ms Latency for Identity)
        if topic == "persona":
            logger.info("📖 Semantic Router: Injecting Syntheta Lore Book...")
            lore_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../nlu/syntheta_lore.md'))
            try:
                with open(lore_path, 'r', encoding='utf-8') as f:
                    context = f.read()
            except Exception as e:
                logger.warning(f"⚠️ Lore Book not found: {e}")
            telemetry["rag_lat_ms"] = 0

        # Route B: The Vector DB (Only for Codebase/Technical Queries)
        elif self.knowledge and topic == "syntheta":
            knowledge_start = time.perf_counter()
            logger.info("🔍 Semantic Router: Routing to RAG Database...")
            context = self.knowledge.get_context(text, top_k=5, rerank_k=2) 
            telemetry["rag_lat_ms"] = round((time.perf_counter() - knowledge_start) * 1000, 2)
            
        # Route C: General Chat (Bypass Context entirely)
        else:
            telemetry["rag_lat_ms"] = 0

        # 🟢 STEP 3: Reflex & Unified Confirmation Check
        plan = self.pi.process_query(sat_id, text)
        
        if plan:
            intent = plan.get("intent")
            
            if intent == "RESUME_CONFIRMED":
                logger.info(f"✅ State Sync: Resume confirmed for Sat {sat_id}")
                self.handle_resume_confirmation(sat_id, True)
                return
            if intent == "RESUME_CANCELLED":
                logger.info(f"❌ State Sync: Resume cancelled for Sat {sat_id}")
                self.handle_resume_confirmation(sat_id, False)
                return

            if intent and intent != "unknown":
                if intent == "RECOGNITION_QUESTION" and topic:
                     logger.info(f"⏳ Ambiguous reflex match. Triggering Topic Filler: {topic}")
                     self.emitter.emit("play_topic_filler", sat_id, {"topic": topic})
                
                self._execute_plan(sat_id, plan, telemetry)
                
                # 🟢 NEW: Post-Interruption Branching (Reflex)
                if self.state.resume_pending.get(sat_id):
                    state_dict = self.state.cognitive.get(sat_id, {})
                    active_subject = state_dict.get("active_subject", "general")
                    
                    if active_subject and active_subject != "general":
                        resume_prompt = f"Would you like me to finish what I was saying about {active_subject}?"
                    else:
                        resume_prompt = "Would you like me to finish what I was saying?"
                    
                    logger.info(f"⏸️ Reflex executed. Prompting user to resume: '{resume_prompt}'")
                    # Send TTS with force_listen=True so user can say Yes/No
                    self._speak(sat_id, resume_prompt, force_listen=True)
                return

        # 🟢 NEW: Post-Interruption Branching (Non-Reflex)
        # The user asked a new question, shifting the topic. Clear the paused audio.
        if self.state.resume_pending.get(sat_id):
            logger.info(f"🔄 Topic shifted by user. Dropping paused audio for Sat {sat_id}.")
            self.state.reset_interruption(sat_id)

        # 🟢 STEP 4: Cognitive Path (LLM Pipeline)
        self.state.is_conversation = True
        
        if topic:
             logger.info(f"⏳ Triggering Topic Filler for: {topic}")
             self.emitter.emit("play_topic_filler", sat_id, {"topic": topic})

        # ✅ Enable Music Bridge safety net
        self.is_thinking = True 
        llm_start = time.perf_counter()
        
        # Inject context if the Semantic Router found any
        enriched_input = processed['input']
        if context:
            if topic == "persona":
                enriched_input = f"Use this background lore about yourself to answer naturally:\n\n{context}\n\nUSER QUERY: {processed['input']}"
            elif topic == "syntheta":
                enriched_input = f"Use the following project context to answer the user query.\n\nCONTEXT FROM PROJECT FILES:\n{context}\n\nUSER QUERY: {processed['input']}"
        
        self.state.update_context(sat_id, processed['input'], processed['entities'])
        packet = self.state.build_golden_packet(sat_id, enriched_input, processed.get('emotion', 'neutral'))
        
        try:
            llm_response_dict = self.llm.generate(packet)
            
            if isinstance(llm_response_dict, dict):
                llm_response = llm_response_dict.get("response", "I lost my train of thought.")
                active_subject = llm_response_dict.get("active_subject", "general")
            else:
                llm_response = str(llm_response_dict)
                active_subject = "general"
                
            telemetry["llm_lat_ms"] = round((time.perf_counter() - llm_start) * 1000, 2)
            
            self.state.commit_assistant_response(sat_id, llm_response, active_subject)
            
            # 🟢 THE FIX: Pass force_listen=True so the mic opens naturally when playback finishes
            self._speak(sat_id, llm_response, force_listen=True, telemetry=telemetry)
            
            self.is_thinking = False 
            
        except Exception as e:            
            logger.error(f"❌ LLM Pipeline Error: {e}")
            self.is_thinking = False
            self._speak(sat_id, "I'm having trouble connecting to my brain right now.")    
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
            if isinstance(plan["execute"], str) and "." in plan["execute"]:
                self.ha.execute(plan["execute"])
        force_listen = plan.get("force_listen", False)
        if plan.get("speak"):
            self._speak(sat_id, plan["speak"], force_listen, telemetry=telemetry)
        elif force_listen and self.comms:
            self.comms.send_command(sat_id, {"cmd": "force_listen", "timeout": 4})

    def _speak(self, sat_id, text, force_listen=False, telemetry=None):
        # 🟢 PURE SPEECH: Cache layer removed, goes direct to tts_engine
        if self.tts:
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
        
        # 🟢 FIX: Set deterministic trigger flag instead of blind timer
        self.pending_force_listen[sat_id] = force_listen

        logger.info(f"🔈 Handing off playback to Go Bridge for Sat {sat_id}")
        self.emitter.emit("play_file", sat_id, {"filepath": path})
        
        # ❌ REMOVED the old threading.Timer for force_listen
    def _report_latency(self, tele):
            if not ENABLE_LATENCY_TELEMETRY: return
            
            stt = tele.get("stt_lat_ms", 0)
            brain = tele.get("brain_lat_ms", 0)
            rag = tele.get("rag_lat_ms", 0) # 🟢 NEW
            llm = tele.get("llm_lat_ms", 0)
            tts = tele.get("tts_lat_ms", 0)
            
            start = tele.get("start_time", time.perf_counter())
            total = round((time.perf_counter() - start) * 1000, 2)
            
            print("\n" + "="*45)
            print(f"📊 OMEGA PERFORMANCE REPORT (Total: {total}ms)")
            print("-" * 45)
            print(f" 🟢 STT (Whisper):   {stt:>7} ms")
            print(f" 🔵 Brain (NLU):     {brain:>7} ms")
            print(f" 📚 RAG (BGE-M3):    {rag:>7} ms") # 🟢 NEW
            print(f" 🟣 LLM (Ollama):    {llm:>7} ms")
            print(f" 🟠 TTS (Kokoro):    {tts:>7} ms")
            print("="*45 + "\n")