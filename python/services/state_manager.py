import time
import queue
import logging
import re
import json
import requests
import numpy as np
import wave
import contextlib
import sys
import os
from typing import Dict

sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))
from core.data_models import GoldenPacket, CognitiveState

logger = logging.getLogger("StateManager")

SUMMARIZER_MODEL = "llama3.2:1b"    # Lightweight — background only
OLLAMA_CHAT_URL  = "http://localhost:11434/api/chat"
MAX_HISTORY_PAIRS = 8               # Pairs before 1B summarizer fires


class EngineState:
    def __init__(self):
        # ── Audio ─────────────────────────────────────────────
        self.audio_queue   = queue.Queue(maxsize=500)
        self.audio_buffers = {}     # {sat_id: bytearray}
        self.buffers       = {}     # {sat_id: bytearray}
        self.calib_buffer  = {}     # {sat_id: [rms, ...]}
        self.thresholds    = {}     # {sat_id: float}

        # ── Timers ─────────────────────────────────────────────
        self.last_active_time = {}  # {sat_id: time} — resets on every audio packet
        # 🟢 last_interaction_time — resets ONLY on actual user speech
        # NightWatchman reads this for idle detection
        self.last_interaction_time = 0.0

        self.silence_start  = {}
        self.follow_up_start = {}

        # ── Session ────────────────────────────────────────────
        self.session_start_time = 0.0
        self.is_conversation    = False
        self.deaf_until         = 0.0
        self.skip_byte_counter  = 0     # Bytes to skip after wake collision

        # ── Modes ──────────────────────────────────────────────
        self.state        = {}
        self.session_mode = {}
        self.wake_volume  = {}

        # ── Interruption & Resume ──────────────────────────────
        self.session_origins  = {}
        self.playback_info    = {}
        self.interrupted_state = {}
        self.resume_pending   = {}
        self.is_muted         = False

        # ── Cognitive State (per satellite) ───────────────────
        self.cognitive: Dict[int, CognitiveState] = {}

    # ----------------------------------------------------------
    # COGNITIVE STATE INIT
    # ----------------------------------------------------------
    def _init_cognitive_state(self, sat_id: int):
        if sat_id not in self.cognitive:
            self.cognitive[sat_id] = {
                "topic":          "general",
                "entities":       {},
                "history_buffer": [],   # [{role, content}, ...]
                "summary":        "",   # Compressed summary of older turns
                "last_interaction": 0.0,
                "active_subject": "general",
                "is_active":      False,
            }

    # ----------------------------------------------------------
    # CONTEXT UPDATE — called by engine after transcription
    # ----------------------------------------------------------
    def update_context(self, sat_id: int, user_text: str,
                       new_entities: dict, force_reset: bool = False):
        self._init_cognitive_state(sat_id)

        if force_reset:
            logger.info(f"🔄 Context Pivot for Sat {sat_id}.")
            self.cognitive[sat_id]["topic"]          = "general"
            self.cognitive[sat_id]["entities"]       = {}
            self.cognitive[sat_id]["history_buffer"] = []
            self.cognitive[sat_id]["summary"]        = ""

        if new_entities:
            self.cognitive[sat_id]["entities"].update(new_entities)

        self._append_history(sat_id, "user", user_text)
        self.cognitive[sat_id]["last_interaction"] = time.time()

        # 🟢 Update global interaction clock — NightWatchman idle check reads this
        self.last_interaction_time = time.time()

    def get_recent_context(self, sat_id: int):
        self._init_cognitive_state(sat_id)
        return self.cognitive[sat_id]["history_buffer"]

    def commit_assistant_response(self, sat_id: int, text: str,
                                  active_subject: str = "general"):
        """
        Saves assistant response to history.
        Triggers 1B summarizer if history exceeds MAX_HISTORY_PAIRS.
        active_subject stored for resume prompt generation.
        """
        self._init_cognitive_state(sat_id)
        self._append_history(sat_id, "assistant", text)
        self.cognitive[sat_id]["active_subject"] = active_subject

        # Trigger summarizer if history is getting long
        pairs = len(self.cognitive[sat_id]["history_buffer"]) // 2
        if pairs >= MAX_HISTORY_PAIRS:
            import threading
            threading.Thread(
                target=self._summarize_history,
                args=(sat_id,),
                daemon=True
            ).start()

    def _append_history(self, sat_id: int, role: str, text: str):
        """Appends turn to history buffer. Hard cap at MAX_HISTORY_PAIRS * 2 entries."""
        buffer = self.cognitive[sat_id]["history_buffer"]
        buffer.append({"role": role, "content": text})
        hard_cap = MAX_HISTORY_PAIRS * 2
        if len(buffer) > hard_cap:
            self.cognitive[sat_id]["history_buffer"] = buffer[-hard_cap:]

    # ----------------------------------------------------------
    # 1B HISTORY SUMMARIZER — fires in background thread
    # ----------------------------------------------------------
    def _summarize_history(self, sat_id: int):
        """
        Compresses the oldest half of history into a summary string.
        Uses llama3.2:1b — lightweight, never blocks main LLM.
        Runs in a daemon thread so it never blocks engine.
        """
        self._init_cognitive_state(sat_id)
        buffer = self.cognitive[sat_id]["history_buffer"]

        if len(buffer) < MAX_HISTORY_PAIRS * 2:
            return  # Nothing to compress yet

        # Take the oldest half to summarise, keep the newest half live
        half       = len(buffer) // 2
        old_turns  = buffer[:half]
        keep_turns = buffer[half:]

        history_text = "\n".join(
            f"{t['role'].upper()}: {t['content']}" for t in old_turns
        )
        existing_summary = self.cognitive[sat_id].get("summary", "")

        prompt = (
            f"Summarise this conversation in 2-3 sentences. "
            f"Keep all personal facts, names, topics discussed. "
            f"Third person style.\n\n"
            f"{'PRIOR SUMMARY: ' + existing_summary + chr(10) if existing_summary else ''}"
            f"CONVERSATION:\n{history_text}\n\nSUMMARY:"
        )

        try:
            payload = {
                "model":      SUMMARIZER_MODEL,
                "messages":   [{"role": "user", "content": prompt}],
                "stream":     False,
                "keep_alive": -1,
                "options":    {"temperature": 0.0, "num_predict": 150}
            }
            res     = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=20.0)
            summary = res.json().get("message", {}).get("content", "").strip()
            summary = re.sub(r'<think>.*?</think>', '', summary, flags=re.DOTALL).strip()

            if summary:
                self.cognitive[sat_id]["summary"]        = summary
                self.cognitive[sat_id]["history_buffer"] = keep_turns
                logger.info(f"📝 History summarised for Sat {sat_id}.")

        except Exception as e:
            logger.error(f"⚠️ Summarizer failed for Sat {sat_id}: {e}")

    # ----------------------------------------------------------
    # GOLDEN PACKET FACTORY
    # ----------------------------------------------------------
    def build_golden_packet(self, sat_id: int, user_text: str,
                             emotion: str,
                             memory_context: str = "") -> GoldenPacket:
        """
        Assembles the complete GoldenPacket for LLMBridge.

        memory_context — injected by engine from get_context_fast()
                         (JSON bucket node facts, current session)
        memory_tank    — filled by router/engine from SQL nomic retrieval
                         or web_data (set after enrich_packet)
        """
        self._init_cognitive_state(sat_id)
        state = self.cognitive[sat_id]

        # Build history string — include summary of older turns if present
        history_parts = []
        summary = state.get("summary", "")
        if summary:
            history_parts.append(f"[Earlier context]: {summary}")

        for turn in state["history_buffer"]:
            history_parts.append(
                f"{turn['role'].upper()}: {turn['content']}")

        history_str = "\n".join(history_parts)

        # Update interaction clock
        self.last_interaction_time = time.time()

        return {
            # Core identity
            "role":          "You are Syntheta, a concise and helpful AI.",
            "ctx":           state["topic"],
            "emotion":       emotion,
            "entities":      state.get("entities", {}),

            # Input
            "input":         user_text,

            # History (engine will override with sliding window)
            "history":       history_str,

            # Memory layers — populated by engine + router
            "memory_context": memory_context,  # JSON bucket facts (current session)
            "memory_tank":    "",              # Set by engine after enrich_packet

            # Routing — set by LibrarianRouter.enrich_packet()
            "route_taken":        "general_no_web",
            "needs_memory":       False,
            "matched_memory_node": None,
            "web_data":           None,

            # Model selection — default, router can override
            "model":       "llama3.2:3b",

            # Abort check — set by engine for barge-in detection
            "abort_check": None,
        }

    # ----------------------------------------------------------
    # PLAYBACK TRACKING
    # ----------------------------------------------------------
    def track_playback(self, sat_id: int, filepath: str):
        self.playback_info[sat_id] = {
            "file": filepath, "start_time": time.time()}

    def clear_playback(self, sat_id: int):
        self.playback_info.pop(sat_id, None)
        self.resume_pending.pop(sat_id, None)

    def snapshot_playback(self, sat_id: int):
        if sat_id in self.playback_info:
            info     = self.playback_info[sat_id]
            duration = time.time() - info["start_time"]
            self.interrupted_state[sat_id] = {
                "file": info["file"], "duration": duration}
            logger.info(f"⏸️  Audio paused at {duration:.2f}s for Sat {sat_id}")
            self.playback_info.pop(sat_id, None)
            self.resume_pending[sat_id] = True

    def reset_interruption(self, sat_id: int):
        self.interrupted_state.pop(sat_id, None)
        self.resume_pending.pop(sat_id, None)
        self.playback_info.pop(sat_id, None)

    # ----------------------------------------------------------
    # UTILITIES
    # ----------------------------------------------------------
    def register_wake_event(self, sat_id: int):
        self.deaf_until              = 0.0
        self.last_active_time[sat_id] = time.time()
        self.last_interaction_time   = time.time()
        self.session_mode[sat_id]    = "LISTENING"
        self.reset_interruption(sat_id)
        logger.info(f"⏰ Session Reset for Sat {sat_id}")

    def update_noise_floor(self, sat_id: int, floor_val: float):
        self.thresholds[sat_id] = floor_val
        logger.info(f"🧠 Noise Floor updated: Sat {sat_id} = {floor_val}")

    def get_wav_duration(self, filepath: str) -> float:
        try:
            with contextlib.closing(wave.open(filepath, 'r')) as f:
                return f.getnframes() / float(f.getframerate())
        except Exception:
            return 0.0

    def get_buffer(self, sat_id: int) -> bytearray:
        if sat_id not in self.buffers:
            self.buffers[sat_id] = bytearray()
        return self.buffers[sat_id]

    def calculate_rms(self, pcm_bytes: bytes):
        if not pcm_bytes:
            return 0.0, np.array([], dtype=np.float32)
        audio_float = (np.frombuffer(pcm_bytes, dtype=np.int16)
                       .astype(np.float32) / 32768.0)
        rms = np.sqrt(np.mean(audio_float ** 2))
        return rms, audio_float