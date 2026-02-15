import os
import logging
import subprocess
import uuid
import wave
import sys
import time
import contextlib

logger = logging.getLogger("TTS")

# ==========================================
# ⚙️ PATH CONFIGURATION
# ==========================================
# Current: python/tts_engine.py
# Root:    python/../ (syntheta-hub/)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../'))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
TEMP_DIR = os.path.join(ASSETS_DIR, "temp")
PIPER_ROOT = os.path.join(BASE_DIR, "piper") 

# Binary Selection
if sys.platform == "win32":
    PIPER_EXE = os.path.join(PIPER_ROOT, "piper.exe")
else:
    PIPER_EXE = os.path.join(PIPER_ROOT, "piper")

# Model Path
MODEL_DIR = os.path.join(BASE_DIR, "assets", "models", "piper")
PIPER_MODEL_NAME = "en_US-amy-medium.onnx"
PIPER_MODEL_PATH = os.path.join(MODEL_DIR, PIPER_MODEL_NAME)

class TTSEngine:
    def __init__(self):
        # 1. Verification
        if not os.path.exists(PIPER_MODEL_PATH):
            logger.error(f"❌ Voice model missing: {PIPER_MODEL_PATH}")
            logger.error(f"   (Please download {PIPER_MODEL_NAME} to {MODEL_DIR})")

        if not os.path.exists(PIPER_EXE):
             logger.error(f"❌ Piper Binary missing: {PIPER_EXE}")

        # 2. Create Temp Folder
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        # 3. Simple In-Memory Cache for System Phrases
        # Stores path to generated files: {"okay": "/tmp/tts_83d.wav"}
        self.phrase_cache = {} 
        
        # 4. Calibration Data (Learning Speed)
        # Default start: Piper generates ~60ms of audio per character
        self.ms_per_char = 60.0 
        
        logger.info(f"🟣 TTS Engine Online | Voice: {PIPER_MODEL_NAME}")
        
    def estimate_duration(self, text):
        """
        Bug #3: Guesses how long a sentence will take to speak.
        Used to pick the right filler ("Umm" vs "Let me see...").
        Returns: Duration in seconds.
        """
        if not text: return 0.0
        # Simple heuristic: char count * learned speed
        # "Hello world" (11 chars) * 60ms = 660ms = 0.6s
        estimated_ms = len(text) * self.ms_per_char
        return estimated_ms / 1000.0

    def generate_to_file(self, text: str, output_path=None):
        """
        Generates WAV file. 
        Checks cache first for speed.
        """
        if not text or not text.strip():
            return None
            
        clean_text = text.strip()

        # 1. Check Cache (For "Yes", "No", "Stop", etc.)
        if clean_text in self.phrase_cache:
            cached_path = self.phrase_cache[clean_text]
            if os.path.exists(cached_path):
                # logger.debug(f"[TTS] Cache Hit: '{clean_text[:15]}...'")
                return cached_path

        # 2. Setup Filename
        if not output_path:
            filename = f"tts_{uuid.uuid4().hex[:8]}.wav"
            output_path = os.path.join(TEMP_DIR, filename)

        # 3. Generate
        cmd = [PIPER_EXE, "--model", PIPER_MODEL_PATH, "--output_file", output_path]

        try:
            # Check if model exists before running to avoid obscure errors
            if not os.path.exists(PIPER_MODEL_PATH):
                return None

            subprocess.run(
                cmd,
                input=clean_text,
                text=True, 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            
            # 4. Learn Speed (Bug #3 & #7)
            # We measure how long the AUDIO is, not how long it took to generate
            audio_len = self._get_wav_duration(output_path)
            if audio_len > 0:
                # Update moving average of speech speed (Weighted: 90% old, 10% new)
                current_speed = (audio_len * 1000) / len(clean_text)
                self.ms_per_char = (self.ms_per_char * 0.9) + (current_speed * 0.1)
            
            # 5. Append Silence (Prevent clipping at end of sentence)
            self._append_silence(output_path, duration_ms=150)

            # 6. Cache short phrases (under 20 chars) for instant replay
            if len(clean_text) < 20:
                self.phrase_cache[clean_text] = output_path

            return output_path

        except subprocess.CalledProcessError:
            logger.error(f"❌ Piper failed to generate audio.")
            return None
        except Exception as e:
            logger.error(f"[TTS] Generation Error: {e}")
            return None

    def _get_wav_duration(self, file_path):
        """Reads actual audio length from WAV header"""
        try:
            with contextlib.closing(wave.open(file_path, 'r')) as f:
                frames = f.getnframes()
                rate = f.getframerate()
                return frames / float(rate)
        except:
            return 0.0

    def _append_silence(self, file_path: str, duration_ms: int = 150):
        """Adds silence to the end of the file to prevent cutoff"""
        if not os.path.exists(file_path): return
        try:
            with wave.open(file_path, 'rb') as wav_in:
                params = wav_in.getparams()
                frames = wav_in.readframes(wav_in.getnframes())
                
                num_silent_frames = int(params.framerate * (duration_ms / 1000.0))
                
                # Align to block boundary (Critical for 16-bit Stereo safety)
                block_align = params.nchannels * params.sampwidth
                if num_silent_frames % block_align != 0:
                    num_silent_frames += (block_align - (num_silent_frames % block_align))
                
                silence_data = b'\x00' * num_silent_frames * block_align

            with wave.open(file_path, 'wb') as wav_out:
                wav_out.setparams(params)
                wav_out.writeframes(frames + silence_data)
        except Exception as e:
            logger.warning(f"[TTS] Failed to append silence: {e}")