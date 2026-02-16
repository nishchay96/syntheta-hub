import logging
import os
import numpy as np
from faster_whisper import WhisperModel
from .config import WHISPER_PROMPT

logger = logging.getLogger("Whisper")

class AudioTranscriber:
    def __init__(self, model_size="base.en", device="cuda"):
        """
        🚀 LATENCY FIX: Migrated from 'cpu' to 'cuda' (GPU).
        Switched compute_type to 'float16' for optimized NVIDIA performance.
        """
        # 1. Calculate Path to Offline Model
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
        model_path = os.path.join(base_dir, "assets", "models", "whisper-base-en")

        logger.info(f"Loading Whisper from: {model_path} on {device}...")
        
        try:
            # 2. Check if local model exists
            if os.path.exists(model_path) and os.listdir(model_path):
                # 🟢 OPTIMIZED: Loaded on GPU with float16 precision
                self.model = WhisperModel(
                    model_path, 
                    device=device, 
                    compute_type="float16"
                )
                logger.info("✅ Whisper Loaded Successfully on GPU (Offline Mode).")
            else:
                # Fallback to default cache
                logger.warning(f"⚠️ Local model not found at {model_path}. Trying default cache...")
                self.model = WhisperModel(
                    model_size, 
                    device=device, 
                    compute_type="float16"
                )
                
        except Exception as e:
            logger.critical(f"Failed to load Whisper: {e}")
            logger.critical("TIP: Run 'tools/download_offline_assets.py' inside the venv first!")
            raise e

    def transcribe(self, audio_bytes):
        """
        Takes raw 16kHz PCM bytes, converts to float32, and transcribes.
        Returns: (text, confidence_score_0_to_1)
        """
        if not audio_bytes or len(audio_bytes) < 3200: 
            return "", 0.0

        # Convert raw bytes to float32 (Whisper expects normalized floats -1.0 to 1.0)
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = self.model.transcribe(
            audio_np, 
            beam_size=5, 
            language="en", 
            vad_filter=False, # Alpha/Gatekeeper manage timing
            initial_prompt=WHISPER_PROMPT 
        )
        
        full_text = []
        avg_logprob = 0.0
        count = 0
        
        for segment in segments:
            full_text.append(segment.text)
            avg_logprob += segment.avg_logprob
            count += 1
            
        if count == 0:
            return "", 0.0
            
        # Mathematical translation of logarithmic probability into a 0.0 to 1.0 percentage
        confidence = np.exp(avg_logprob / count)
        text = " ".join(full_text).strip()
        
        return text, confidence