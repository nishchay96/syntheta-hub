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
from datetime import datetime # 🟢 NEW: Time tracking

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

from core.database_manager import DatabaseManager
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
from .audio_tools import pad_audio_file
from tts_engine import TTSEngine
from nlu.router_bridge import LibrarianRouter 

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
            self.tts = TTSEngine()
            logger.info("✅ TTS Engine Online (VRAM Optimized)")
        except Exception as e: 
            logger.warning(f"⚠️ TTS Disabled: {e}")
            self.tts = None
        
        try:
            self.knowledge = KnowledgeManager()
            logger.info("✅ OMEGA Knowledge Engine Online (BGE-M3 + Reranker)")
        except Exception as e:
            logger.error(f"❌ Knowledge Engine failed: {e}")
            self.knowledge = None

        try:
            self.librarian = LibrarianRouter(self.knowledge)
            logger.info("✅ Librarian Router Online (Semantic 3-Way)")
        except Exception as e:
            logger.error(f"❌ Librarian Router failed: {e}")
            self.librarian = None

        self.security_mode = "NORMAL" 
        self.sudo_timer = 0
        self.sudo_challenge_deadline = 0
        self.pending_sudo_cmd = None
        self.hallucinations = list(DEFAULT_HALLUCINATIONS)
        self.ghost_counters = {} 

        self.wwd_timers = {}
        self.pending_force_listen = {} 
        
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
        
        self.pending_force_listen[sat_id] = False
        
        self.state.session_start_time = time.time()
        self.state.is_conversation = False
        self.state.last_active_time[sat_id] = time.time()
        self.state.session_mode[sat_id] = "LISTENING"
        
        self.pi.start_new_session(sat_id)

    def on_calibration_update(self, sat_id, floor):
        self.gatekeeper.update_calibration(sat_id, floor)

    def on_playback_finished(self, sat_id, filename):
        base_file = os.path.basename(filename)
        logger.info(f"🎵 Playback Finished [Sat {sat_id}]: {base_file}")
        
        is_interrupted = self.state.resume_pending.get(sat_id, False)
        
        if not is_interrupted and "temp" in filename:
            try:
                if os.path.exists(filename):
                    os.remove(filename)
                    logger.info(f"🧹 Garbage Collection: Removed finished temp file {base_file}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to remove temp file {base_file}: {e}")
        elif is_interrupted and "temp" in filename:
            logger.info(f"🛡️ Preserving interrupted file for potential resume: {base_file}")

        self.state.clear_playback(sat_id)
        
        if "satellite_connect" in base_file:
            logger.info(f"📢 Calibration Warning Finished. Waiting for room silence...")
            time.sleep(1.5) 
            
            if self.comms:
                logger.info(f"⚙️ Sending Calibration Command to Sat {sat_id}")
                self.comms.send_command(sat_id, {"cmd": "calibrate"})
            return

        if "filler_" in base_file and self.is_thinking:
            logger.info(f"⏳ Filler finished but LLM is thinking. Bridging with music.")
            self.emitter.emit("play_music", sat_id, {"filename": "bridge.wav"})

        if self.pending_force_listen.get(sat_id):
            logger.info(f"🎤 Playback complete. Synchronizing Python state and opening mic for Sat {sat_id}")
            
            self.state.audio_buffers[sat_id] = b""
            self.state.deaf_until = 0.0
            self.state.last_active_time[sat_id] = time.time()
            self.state.session_mode[sat_id] = "LISTENING"
            self.state.skip_byte_counter = 0 
            
            if self.comms:
                threading.Timer(0.5, lambda: self.comms.send_command(sat_id, {"cmd": "force_listen", "timeout": 4})).start()
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
        try:
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
            
        except Exception as e:
            logger.error(f"❌ Pipeline thread critically failed: {e}", exc_info=True)
            self._speak(sat_id, "I hit an internal error in my processing pipeline.")
        finally:
            self.is_thinking = False

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
        
        if topic == "persona":
            logger.info("📖 Semantic Router: Injecting Syntheta Lore Book...")
            lore_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../nlu/syntheta_lore.md'))
            try:
                with open(lore_path, 'r', encoding='utf-8') as f:
                    context = f.read()
            except Exception as e:
                logger.warning(f"⚠️ Lore Book not found: {e}")
            telemetry["rag_lat_ms"] = 0

        elif self.knowledge and topic == "syntheta":
            knowledge_start = time.perf_counter()
            logger.info("🔍 Semantic Router: Routing to RAG Database...")
            context = self.knowledge.get_context(text, top_k=5, rerank_k=2) 
            telemetry["rag_lat_ms"] = round((time.perf_counter() - knowledge_start) * 1000, 2)
            
        else:
            telemetry["rag_lat_ms"] = 0
            
        # 🟢 STEP 3: Reflex & Unified Confirmation Check
        plan = self.pi.process_query(sat_id, text)
        
        if plan:
            plan["raw_input"] = text  # 🟢 FIX: Pack exact text for logging telemetry
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
                return

        # 🟢 STEP 4: Cognitive Path (LLM Pipeline)
        self.state.is_conversation = True
        
        # 🟢 FIX: WRITE-AHEAD LOGGING
        wal_payload = {
            "user_query": processed['input'],
            "llm_response": "PENDING_GENERATION",
            "topic": topic,
            "entities": processed.get('entities', {}),
            "active_subject": "general"
        }
        task_id = DatabaseManager().create_memory_task(wal_payload)
        
        if topic:
             logger.info(f"⏳ Triggering Topic Filler for: {topic}")
             self.emitter.emit("play_topic_filler", sat_id, {"topic": topic})

        self.is_thinking = True 
        llm_start = time.perf_counter()
        
        # 🟢 FIX: TIME/LOCATION METADATA HEADER
        now = datetime.now()
        system_meta = f"[SYSTEM DATA] Current Time: {now.strftime('%A, %B %d, %Y')} at {now.strftime('%I:%M %p')}. Location: Guwahati, Assam, India."
        
        enriched_input = processed['input']
        if context:
            if topic == "persona":
                enriched_input = f"Use this background lore about yourself to answer naturally:\n\n{context}\n\nUSER QUERY: {processed['input']}"
            elif topic == "syntheta":
                enriched_input = f"Use the following project context to answer the user query.\n\nCONTEXT FROM PROJECT FILES:\n{context}\n\nUSER QUERY: {processed['input']}"
        
        enriched_input = f"{system_meta}\n\n{enriched_input}"
        
        self.state.update_context(sat_id, processed['input'], processed['entities'])
        packet = self.state.build_golden_packet(sat_id, enriched_input, processed.get('emotion', 'neutral'))
        
        if self.librarian:
            packet = self.librarian.enrich_packet(packet)
            
            route = packet.get('route_taken')
            if route in ["live_web_search", "reflex_action"]:
                openclaw_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../assets/openclaw_cache.json'))
                if os.path.exists(openclaw_path):
                    try:
                        with open(openclaw_path, 'r', encoding='utf-8') as f:
                            live_data = f.read()
                            if live_data.strip():
                                packet['history'] = f"--- LIVE SYSTEM DATA ---\n{live_data}\n\n" + packet.get('history', '')
                                logger.info("🌐 Injected OpenClaw Live Data into context.")
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to read OpenClaw cache: {e}")

        try:
            llm_response_dict = self.llm.generate(packet)
            
            if isinstance(llm_response_dict, dict):
                llm_response = llm_response_dict.get("response", "I lost my train of thought.")
                active_subject = llm_response_dict.get("active_subject", "general")
                is_action = llm_response_dict.get("is_action", False)
                ha_execute = llm_response_dict.get("execute", None)
            else:
                llm_response = str(llm_response_dict)
                active_subject = "general"
                is_action = False
                ha_execute = None
                
            telemetry["llm_lat_ms"] = round((time.perf_counter() - llm_start) * 1000, 2)
            
            self.state.commit_assistant_response(sat_id, llm_response, active_subject)
            
            if is_action or ha_execute:
                logger.info(f"🤖 LLM generated an Action: {ha_execute}")
                llm_plan = {
                    "intent": "LLM_ACTION",
                    "execute": ha_execute,
                    "speak": llm_response,
                    "force_listen": False,
                    "raw_input": processed['input'] # 🟢 FIX: Pack exact text for logging
                }
                self._execute_plan(sat_id, llm_plan, telemetry=telemetry)
            else:
                if self.state.resume_pending.get(sat_id):
                    logger.info(f"🔄 Topic shifted by user. Dropping paused audio for Sat {sat_id}.")
                    self.state.reset_interruption(sat_id)
                
                self._speak(sat_id, llm_response, force_listen=True, telemetry=telemetry)
                
                try:
                    memory_payload = {
                        "user_query": processed['input'],
                        "llm_response": llm_response,
                        "topic": topic,
                        "entities": processed.get('entities', {}),
                        "active_subject": active_subject
                    }
                    # 🟢 FIX: FINALIZE WAL TASK
                    DatabaseManager().update_memory_task(task_id, memory_payload)
                    logger.info(f"💾 Interaction {task_id} safely spooled to memory_queue.")
                except Exception as e:
                    logger.error(f"⚠️ Failed to spool interaction to database: {e}")
            
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

        original_file = interrupted.get("file")

        if original_file and os.path.exists(original_file):
            logger.info(f"🔄 Resuming full audio from the top: {os.path.basename(original_file)}")
            time.sleep(1.0)
            self._speak_file(sat_id, original_file, force_listen=True)
            self.state.reset_interruption(sat_id)
        else:
            logger.warning(f"⚠️ Resume failed: Source file missing '{original_file}'")
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
                
        base_speech = plan.get("speak")
        force_listen = plan.get("force_listen", False)
        
        has_action = bool(plan.get("execute"))
        
        # 🟢 FIX: LOG EXECUTED REFLEX TO DATABASE
        if has_action:
            DatabaseManager().insert_reflex_telemetry(
                sat_id, 
                plan.get("intent", "UNKNOWN"), 
                plan.get("raw_input", "N/A"), 
                plan.get("execute", {})
            )
        
        if self.state.resume_pending.get(sat_id):
            if has_action:
                state_dict = self.state.cognitive.get(sat_id, {})
                active_subject = state_dict.get("active_subject", "general")
                
                if active_subject and active_subject != "general":
                    resume_prompt = f"Would you like me to finish what I was saying about {active_subject}?"
                else:
                    resume_prompt = "Would you like me to finish what I was saying?"
                
                final_speech = f"{base_speech}. {resume_prompt}" if base_speech else resume_prompt
                logger.info(f"⏸️ Action completed. Prompting user to resume: '{final_speech}'")
                
                self._speak(sat_id, final_speech, force_listen=True, telemetry=telemetry)
            else:
                logger.info("⏸️ Clarification asked. Suppressing resume prompt until action completes.")
                if base_speech:
                    self._speak(sat_id, base_speech, force_listen=True, telemetry=telemetry)
        else:
            if base_speech:
                self._speak(sat_id, base_speech, force_listen, telemetry=telemetry)
            elif force_listen and self.comms:
                self.comms.send_command(sat_id, {"cmd": "force_listen", "timeout": 4})

    def _speak(self, sat_id, text, force_listen=False, telemetry=None):
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

        if sat_id in self.wwd_timers: 
            self.wwd_timers[sat_id].cancel()
            
        if not force_listen:
            self.wwd_timers[sat_id] = threading.Timer(silent_zone, 
                lambda: self.comms.send_command(sat_id, {"cmd": "wwd_mode", "value": "normal"}))
            self.wwd_timers[sat_id].start()

        self.state.track_playback(sat_id, path)
        self.state.deaf_until = time.time() + duration + 0.2
        
        self.pending_force_listen[sat_id] = force_listen

        logger.info(f"🔈 Handing off playback to Go Bridge for Sat {sat_id}")
        self.emitter.emit("play_file", sat_id, {"filepath": path})
        
    def _report_latency(self, tele):
            if not ENABLE_LATENCY_TELEMETRY: return
            
            stt = tele.get("stt_lat_ms", 0)
            brain = tele.get("brain_lat_ms", 0)
            rag = tele.get("rag_lat_ms", 0) 
            llm = tele.get("llm_lat_ms", 0)
            tts = tele.get("tts_lat_ms", 0)
            
            start = tele.get("start_time", time.perf_counter())
            total = round((time.perf_counter() - start) * 1000, 2)
            
            print("\n" + "="*45)
            print(f"📊 OMEGA PERFORMANCE REPORT (Total: {total}ms)")
            print("-" * 45)
            print(f" 🟢 STT (Whisper):   {stt:>7} ms")
            print(f" 🔵 Brain (NLU):     {brain:>7} ms")
            print(f" 📚 RAG (BGE-M3):    {rag:>7} ms") 
            print(f" 🟣 LLM (Ollama):    {llm:>7} ms")
            print(f" 🟠 TTS (Kokoro):    {tts:>7} ms")
            print("="*45 + "\n")