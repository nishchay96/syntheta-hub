import os
import logging
import uuid
import wave
import sys
import time
import contextlib
import numpy as np
import soundfile as sf
import torch
from kokoro import KPipeline

logger = logging.getLogger("TTS")

# ==========================================
# ⚙️ PATH CONFIGURATION
# ==========================================
# Root structure: syntheta-hub/python/tts_engine.py
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
TEMP_DIR = os.path.join(ASSETS_DIR, "temp")

# 🟢 HARDWARE CONFIG: CPU MODE (Saves VRAM for Brain)
DEVICE = 'cpu'

class TTSEngine:
    def __init__(self):
        """Initializes the Kokoro-82M TTS Pipeline and loads the Persona."""
        
        # 1. Setup Temporary Storage
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        # 2. Persistent Phrase Cache (Under 20 chars)
        self.phrase_cache = {} 
        
        # 3. Moving Average Calibration (ms per character)
        # Kokoro is faster, so we adjust the baseline estimate
        self.ms_per_char = 50.0 

        logger.info(f"🚀 Initializing Kokoro-82M on {DEVICE}...")

        try:
            # 4. Load Pipeline (British English Base)
            self.pipeline = KPipeline(lang_code='b', device=DEVICE, repo_id='hexgrad/Kokoro-82M')
            
            # 5. 🟢 LOAD PERSONA: "Mid-20s Cheerful English Female"
            # We mix 'bf_emma' (British, Warm) with 'af_bella' (American, High Energy)
            # This creates a unique accent that sounds professional yet enthusiastic.
            logger.info("⬇️ Loading Voice Blend: bf_emma (60%) + af_bella (40%)")
            voice_british = self.pipeline.load_voice('bf_emma')
            voice_energy = self.pipeline.load_voice('af_bella')
            
            # Blend the tensors
            self.voice_embedding = (voice_british * 0.6) + (voice_energy * 0.4)
            
            logger.info(f"🟣 TTS Engine Online | Persona: Syntheta (Emma-Bella Hybrid)")
            
        except Exception as e:
            logger.error(f"❌ Kokoro Init Failed: {e}")
            self.pipeline = None

    def estimate_duration(self, text):
        """Guesses duration in seconds for filler-selection logic."""
        if not text: return 0.0
        estimated_ms = len(text) * self.ms_per_char
        return estimated_ms / 1000.0

    def generate_to_file(self, text: str, output_path=None):
        """
        Generates a WAV file using Kokoro-82M.
        Direct memory generation -> Disk write (No subprocess overhead).
        """
        if not text or not text.strip():
            return None
            
        if not self.pipeline:
            logger.error("❌ TTS Pipeline not initialized.")
            return None

        clean_text = text.strip()

        # 1. Check Phrase Cache for high-speed replay
        if clean_text in self.phrase_cache:
            cached_path = self.phrase_cache[clean_text]
            if os.path.exists(cached_path):
                return cached_path

        # 2. Define Output Destination
        if not output_path:
            filename = f"tts_{uuid.uuid4().hex[:8]}.wav"
            output_path = os.path.join(TEMP_DIR, filename)

        try:
            # 3. 🟢 GENERATE AUDIO (In-Memory)
            # speed=1.1 adds a youthful, energetic pace
            generator = self.pipeline(
                clean_text, 
                voice=self.voice_embedding, 
                speed=1.1, 
                split_pattern=r'\n+'
            )
            
            # 4. Concatenate Audio Segments
            full_audio = []
            for i, (gs, ps, audio) in enumerate(generator):
                full_audio.append(audio)
            
            if not full_audio:
                return None
                
            audio_data = np.concatenate(full_audio)

            # 5. Write to Disk (24kHz High Fidelity)
            sf.write(output_path, audio_data, 24000)
            
            # 6. Adaptive Speed Learning (Weighted 90/10)
            audio_len = self._get_wav_duration(output_path)
            if audio_len > 0:
                current_speed = (audio_len * 1000) / len(clean_text)
                self.ms_per_char = (self.ms_per_char * 0.9) + (current_speed * 0.1)
            
            # 7. Conditional Cache Enrollment
            if len(clean_text) < 20:
                self.phrase_cache[clean_text] = output_path

            return output_path

        except Exception as e:
            logger.error(f"[TTS] Generation Error: {e}")
            return None

    def _get_wav_duration(self, file_path):
        """Extracts precise duration from the WAV header."""
        try:
            with contextlib.closing(wave.open(file_path, 'r')) as f:
                frames = f.getnframes()
                rate = f.getframerate()
                return frames / float(rate)
        except:
            return 0.0

    # 🛑 DEPRECATED: Retained for internal reference but no longer called by generate_to_file.
    def _append_silence(self, file_path: str, duration_ms: int = 150):
        """Legacy helper: Adds silence to the end of the file."""
        if not os.path.exists(file_path): return
        try:
            with wave.open(file_path, 'rb') as wav_in:
                params = wav_in.getparams()
                frames = wav_in.readframes(wav_in.getnframes())
                num_silent_frames = int(params.framerate * (duration_ms / 1000.0))
                block_align = params.nchannels * params.sampwidth
                if num_silent_frames % block_align != 0:
                    num_silent_frames += (block_align - (num_silent_frames % block_align))
                silence_data = b'\x00' * num_silent_frames * block_align
            with wave.open(file_path, 'wb') as wav_out:
                wav_out.setparams(params)
                wav_out.writeframes(frames + silence_data)
        except Exception as e:
            logger.warning(f"[TTS] Failed to append silence: {e}")