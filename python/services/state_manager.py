import time
import queue
import logging
import numpy as np
import wave
import contextlib
import sys
import os
from typing import Dict, List, Any

# 🔧 PHASE 3: IMPORT DATA MODELS
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))
from core.data_models import GoldenPacket, CognitiveState

logger = logging.getLogger("StateManager")

class EngineState:
    def __init__(self):
        # --- AUDIO DATA ---
        self.audio_queue = queue.Queue(maxsize=500)
        self.audio_buffers = {}     # {sat_id: bytearray}
        self.buffers = {}           # {sat_id: bytearray}
        self.calib_buffer = {}      # {sat_id: [rms, ...]}
        self.thresholds = {}        # {sat_id: float}
        
        # --- TIMERS ---
        self.last_active_time = {}  # {sat_id: time}
        self.silence_start = {}     # {sat_id: time}
        self.follow_up_start = {}   # {sat_id: time}
        
        # --- SESSION & CONVERSATION LOGIC ---
        self.session_start_time = 0.0  
        self.is_conversation = False   

        self.deaf_until = 0.0 # Timestamp: Ignore audio until this time

        # --- MODES ---
        self.state = {}        
        self.session_mode = {}      
        self.wake_volume = {}
        
        # --- INTERRUPTION & RESUME STATE ---
        self.session_origins = {}   
        self.playback_info = {}     
        self.interrupted_state = {} 
        self.resume_pending = {}    
        
        self.is_muted = False

        # ========================================================
        # 🧠 PHASE 3: COGNITIVE STATE (Multi-Room Briefcases)
        # ========================================================
        # 🟢 FIX 1: Make Cognitive State Per-Satellite
        self.cognitive: Dict[int, CognitiveState] = {}

    def _init_cognitive_state(self, sat_id):
        """Helper to ensure a satellite has a brain state initialized."""
        if sat_id not in self.cognitive:
            self.cognitive[sat_id] = {
                "topic": "general",
                "active_subject": "general", # 🟢 NEW: Track the specific subject from the SLM
                "entities": {},
                "history_buffer": [],
                "last_interaction": 0.0,
                "is_active": False
            }

    # ========================================================
    # 🧠 COGNITIVE METHODS (The Judge)
    # ========================================================

    def get_recent_context(self, sat_id, limit=5) -> List[Dict[str, Any]]:
        """
        🟢 NEW: Read-Only Access for PiManager.
        Returns the last 'limit' turns from the cognitive history buffer.
        """
        self._init_cognitive_state(sat_id)
        buffer = self.cognitive[sat_id]["history_buffer"]
        return buffer[-limit:]

    def update_context(self, sat_id, user_text, new_entities, force_reset=False):
        """
        The Judge: Updates History and Entities for a specific Satellite.
        """
        self._init_cognitive_state(sat_id)
        
        # 1. External Topic Pivot (Triggered by PiManager or SemanticBrain)
        # 🟢 FIX 3: Removed brittle substring matching.
        if force_reset:
            logger.info(f"🔄 Context Pivot Triggered for Sat {sat_id}.")
            self.cognitive[sat_id]["topic"] = "general"
            self.cognitive[sat_id]["active_subject"] = "general" # 🟢 NEW: Hard reset the subject
            self.cognitive[sat_id]["entities"] = {} 
            self.cognitive[sat_id]["history_buffer"] = [] # Hard reset
        
        # 2. Update Briefcase
        if new_entities:
            self.cognitive[sat_id]["entities"].update(new_entities)
            
        # 3. Update History 
        # 🟢 FIX 2: We append but explicitly track the role
        self._append_history(sat_id, "user", user_text)
        
        self.cognitive[sat_id]["last_interaction"] = time.time()

    def commit_assistant_response(self, sat_id, text, active_subject="general"):
        """Called after LLM or Reflex generates a reply to save it."""
        self._init_cognitive_state(sat_id)
        self._append_history(sat_id, "assistant", text)
        self.cognitive[sat_id]["active_subject"] = active_subject # 🟢 NEW: Save the specific subject

    def _append_history(self, sat_id, role, text):
        """Keeps history buffer within limits (last 6 turns)."""
        buffer = self.cognitive[sat_id]["history_buffer"]
        buffer.append({"role": role, "content": text})
        
        if len(buffer) > 6:
            self.cognitive[sat_id]["history_buffer"] = buffer[-6:]

    def build_golden_packet(self, sat_id, user_text, emotion) -> GoldenPacket:
        """
        Factory Method: Assembles the Golden Packet for the LLM Bridge.
        """
        self._init_cognitive_state(sat_id)
        state = self.cognitive[sat_id]
        
        history_str = ""
        for turn in state["history_buffer"]:
            history_str += f"{turn['role'].upper()}: {turn['content']}\n"

        return {
            "role": "You are Syntheta, a concise and helpful AI.",
            "ctx": state["topic"],
            "history": history_str,
            "entities": state.get("entities", {}), # Safe get
            "emotion": emotion,
            "input": user_text
        }

    # ========================================================
    # 🔧 ALIGNMENT METHODS & UTILITIES (Unchanged Logic)
    # ========================================================

    def register_wake_event(self, sat_id):
        self.deaf_until = 0.0 
        self.last_active_time[sat_id] = time.time()
        self.session_mode[sat_id] = "LISTENING"
        self.reset_interruption(sat_id)
        logger.info(f"⏰ Session Clock Reset for Sat {sat_id} (Deafness Cleared)")

    def update_noise_floor(self, sat_id, floor_val):
        self.thresholds[sat_id] = floor_val
        logger.info(f"🧠 State Updated: Sat {sat_id} Noise Floor = {floor_val}")

    def get_wav_duration(self, filepath):
        try:
            with contextlib.closing(wave.open(filepath, 'r')) as f:
                frames = f.getnframes()
                rate = f.getframerate()
                return frames / float(rate)
        except Exception:
            return 0.0

    def get_buffer(self, sat_id):
        if sat_id not in self.buffers:
            self.buffers[sat_id] = bytearray()
        return self.buffers[sat_id]

    # ========================================================
    # 🔧 INTERRUPTION & RESUME LOGIC (The Memory)
    # ========================================================

    def reset_interruption(self, sat_id):
        """Clears all interruption tracking for a clean slate."""
        if sat_id in self.interrupted_state or sat_id in self.resume_pending:
            logger.info(f"🧹 Clearing Stale Interruption State for Sat {sat_id}")
            self.interrupted_state.pop(sat_id, None)
            self.resume_pending.pop(sat_id, None)
            self.playback_info.pop(sat_id, None)

    def track_playback(self, sat_id, filepath):
        """Records what is currently playing to allow for snapshots."""
        # 🟢 OPTIMIZED: Removed time.time() tracking as slicing is no longer used
        self.playback_info[sat_id] = {"file": filepath}

    def snapshot_playback(self, sat_id):
        """
        Captures the exact moment audio was cut off.
        Sets resume_pending to True to trigger the Engine's confirmation logic.
        """
        if sat_id in self.playback_info:
            info = self.playback_info[sat_id]
            
            # 🟢 OPTIMIZED: Save only the file path for zero-latency full replay
            self.interrupted_state[sat_id] = {
                "file": info["file"]
            }
            
            # 🟢 TRIGGER: Tell the engine to ask for confirmation next time
            self.resume_pending[sat_id] = True
            
            filename = os.path.basename(info["file"])
            logger.info(f"⏸️  Audio Interrupted. Resume Pending for: {filename}")
            self.playback_info.pop(sat_id, None)

    def calculate_rms(self, pcm_bytes):
        if not pcm_bytes: return 0.0, np.array([], dtype=np.float32)
        audio_float = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean(audio_float**2))
        return rms, audio_float
    # ========================================================
    # 🔧 INTERRUPTION & RESUME LOGIC (The Memory)
    # ========================================================

    def clear_playback(self, sat_id):
        """Called when audio finishes naturally so WWD doesn't hallucinate an interruption."""
        self.playback_info.pop(sat_id, None)