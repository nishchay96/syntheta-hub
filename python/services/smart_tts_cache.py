import os
import logging
import threading
import time
import re
import wave
import contextlib
import random
import concurrent.futures
from collections import Counter

# 🔧 CONFIGURATION
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
ASSET_PATH_DYNAMIC = os.path.join(BASE_DIR, "assets", "fillers", "dynamic") # Learned Habits
ASSET_PATH_STATIC = os.path.join(BASE_DIR, "assets", "fillers")          # Time Buckets

# ⏱️ TUNING: The "No Filler" Zone
# If TTS generation is predicted to take less than this, we go fast (no filler).
LATENCY_THRESHOLD_SEC = 3.0 
IDLE_THRESHOLD_SECONDS = 600

class SmartTTSCache:
    def __init__(self, tts_engine, comms_ref):
        self.logger = logging.getLogger("SmartTTS")
        self.tts = tts_engine
        
        # 🔧 UNWRAP SHIM: Handle the CommsShim from engine.py
        if hasattr(comms_ref, 'comms'):
            self.comms = comms_ref.comms
        else:
            self.comms = comms_ref
            
        # 1. LATENCY BUCKETS (Static Fillers)
        self.buckets = {
            "short": [],   # 3.0s - 5.0s
            "medium": [],  # 5.0s - 8.0s
            "long": []     # 8.0s +
        }
        
        # 2. HABIT MEMORY (Dynamic Fillers)
        self.known_fillers = set()
        self.sentence_buffer = []
        self.buffer_limit = 5
        self.pending_generation = set()
        self.last_activity_time = time.time()
        
        # 3. Parallel Executor
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # 4. Initialize
        os.makedirs(ASSET_PATH_DYNAMIC, exist_ok=True)
        self._load_static_fillers()
        self._load_dynamic_habits()

        # Start Maintenance Loop
        threading.Thread(target=self._idle_maintenance_loop, daemon=True).start()

    def _load_static_fillers(self):
        """Scans assets/fillers for time-bucketed WAVs."""
        if not os.path.exists(ASSET_PATH_STATIC): return
        
        for root, dirs, files in os.walk(ASSET_PATH_STATIC):
            # Skip the dynamic folder to avoid double counting
            if "dynamic" in root: continue
            
            for f in files:
                if f.endswith(".wav"):
                    path = os.path.join(root, f)
                    duration = self._get_wav_duration(path)
                    if duration < 3.0: continue
                    elif duration < 5.0: self.buckets["short"].append(path)
                    elif duration < 8.0: self.buckets["medium"].append(path)
                    else: self.buckets["long"].append(path)

    def _load_dynamic_habits(self):
        """Loads learned short fillers (Okay, Sure)."""
        if not os.path.exists(ASSET_PATH_DYNAMIC): return
        for f in os.listdir(ASSET_PATH_DYNAMIC):
            if f.endswith(".wav"):
                phrase = f.replace(".wav", "").replace("_", " ").lower()
                self.known_fillers.add(phrase)

    def _get_wav_duration(self, path):
        try:
            with contextlib.closing(wave.open(path, 'r')) as f:
                return f.getnframes() / float(f.getframerate())
        except: return 0.0

    # ==========================================================
    # 🧠 THE MASTER ORCHESTRATOR
    # ==========================================================
    def process_and_speak(self, sat_id, text, legacy_play_func):
        """
        Decides between:
        1. Fast Path (Habit Cache)
        2. Slow Path (Parallel JIT Masking)
        """
        clean_text = text.strip()
        if not clean_text: return
        self.last_activity_time = time.time()

        # 1. PREDICT LATENCY
        predicted_latency = self.tts.estimate_duration(clean_text)

        # 🟢 MODE A: FAST TRACK (< 3s)
        if predicted_latency < LATENCY_THRESHOLD_SEC:
            # self.logger.info(f"⚡ Fast Track ({predicted_latency:.2f}s). Checking Habits...")
            self._handle_habit_mode(sat_id, clean_text, legacy_play_func)
            return

        # 🟠 MODE B: JIT MASKING (> 3s)
        self.logger.info(f"🐢 High Latency ({predicted_latency:.2f}s). Engaging Mask.")
        
        # Pick Filler
        filler_path = self._select_bucket_filler(predicted_latency)
        
        if not filler_path:
            # Fallback to normal if no bucket fillers exist
            legacy_play_func(clean_text)
            return

        # 🏁 RACE START: Parallel Generation
        future_audio = self.executor.submit(self.tts.generate_to_file, clean_text)
        
        # Play Filler NOW
        self._play_wav_direct(sat_id, filler_path)
        
        # Wait for TTS and Play
        try:
            generated_path = future_audio.result()
            if generated_path:
                self._play_wav_direct(sat_id, generated_path)
        except Exception as e:
            self.logger.error(f"JIT Gen Failed: {e}")

    # ==========================================================
    # 🧩 LOGIC HANDLERS
    # ==========================================================
    def _handle_habit_mode(self, sat_id, text, play_func):
        """Legacy logic for short sentences/habits."""
        lower_text = text.lower()
        best_match = None
        
        for filler in self.known_fillers:
            pattern = r"^" + re.escape(filler) + r"([\s,\.!])"
            if re.search(pattern, lower_text) or lower_text == filler:
                if best_match is None or len(filler) > len(best_match):
                    best_match = filler

        if best_match:
            # Play Learned Habit
            safe_name = best_match.replace(" ", "_")
            filepath = os.path.join(ASSET_PATH_DYNAMIC, f"{safe_name}.wav")
            
            if os.path.exists(filepath):
                self._play_wav_direct(sat_id, filepath)
            
            # Play Remainder
            remainder = text[len(best_match):].lstrip(" ,.!")
            if remainder:
                play_func(remainder) # Uses Engine to gen remainder
        else:
            play_func(text)
        
        # Trigger Learning
        self.learn(text)

    def _select_bucket_filler(self, latency):
        """Picks a static filler based on predicted delay."""
        candidate_list = []
        if latency < 5.0: candidate_list = self.buckets["short"]
        elif latency < 8.0: candidate_list = self.buckets["medium"]
        else: candidate_list = self.buckets["long"]
            
        if not candidate_list: # Fallback
            if self.buckets["short"]: candidate_list = self.buckets["short"]
            elif self.buckets["medium"]: candidate_list = self.buckets["medium"]
        
        return random.choice(candidate_list) if candidate_list else None

    def _play_wav_direct(self, sat_id, path):
        """Direct stream to Comms (Bypassing Engine Queue)."""
        if not os.path.exists(path): return
        try:
            with open(path, "rb") as f:
                f.seek(44) 
                data = f.read()
            
            # Burst send (Engine thread is busy generating/waiting)
            chunk_size = 1024
            for i in range(0, len(data), chunk_size):
                chunk = data[i:i+chunk_size]
                self.comms.send_audio_frame(sat_id, chunk)
                time.sleep(len(chunk) / 32000.0)
        except Exception as e:
            self.logger.error(f"Direct Play Error: {e}")

    # ==========================================================
    # 🎓 HABIT LEARNING (Bug #7)
    # ==========================================================
    def learn(self, text):
        self.sentence_buffer.append(text)
        if len(self.sentence_buffer) >= self.buffer_limit:
            threading.Thread(target=self._analyze_and_update).start()
            self.sentence_buffer = [] 

    def _analyze_and_update(self):
        start_phrases = []
        for sent in self.sentence_buffer:
            words = sent.split()
            if not words: continue
            
            w1 = re.sub(r'[^\w\s]', '', words[0].lower())
            if w1: start_phrases.append(w1)
            
            if len(words) >= 2:
                w2 = re.sub(r'[^\w\s]', '', words[1].lower())
                if w2: start_phrases.append(f"{w1} {w2}")

        for phrase, count in Counter(start_phrases).items():
            if count >= 3: # Habit Threshold
                if phrase not in self.known_fillers and phrase not in self.pending_generation:
                    self.logger.info(f"⏳ Habit detected: '{phrase}'. Queued.")
                    self.pending_generation.add(phrase)

    def _idle_maintenance_loop(self):
        while True:
            time.sleep(60) 
            if (time.time() - self.last_activity_time) > IDLE_THRESHOLD_SECONDS and self.pending_generation:
                self.logger.info(f"💤 Generating {len(self.pending_generation)} queued habits...")
                for phrase in list(self.pending_generation):
                    if (time.time() - self.last_activity_time) < 10: break
                    self._generate_habit_now(phrase)
                    self.pending_generation.discard(phrase)
                    time.sleep(2) 

    def _generate_habit_now(self, phrase):
        safe_name = phrase.replace(" ", "_")
        save_path = os.path.join(ASSET_PATH_DYNAMIC, f"{safe_name}.wav")
        try:
            self.tts.generate_to_file(phrase, output_path=save_path)
            if os.path.exists(save_path):
                self.known_fillers.add(phrase)
                self.logger.info(f"✅ Cached habit: {safe_name}")
        except: pass