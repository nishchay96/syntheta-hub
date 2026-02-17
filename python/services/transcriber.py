import logging
import os
import numpy as np
import time
from faster_whisper import WhisperModel

# Configuration Imports
try:
    from .config import WHISPER_PROMPT, ASR_MODEL_TYPE, ASR_MODEL_PATH, ASR_DEVICE
except ImportError:
    # Standard fallbacks for isolated testing
    WHISPER_PROMPT = "Syntheta assistant."
    ASR_MODEL_TYPE = "WHISPER"
    ASR_MODEL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../assets/models/whisper-base-en'))
    ASR_DEVICE = "cpu"

logger = logging.getLogger("Transcriber")

class AudioTranscriber:
    def __init__(self):
        self.model_type = ASR_MODEL_TYPE.upper()
        self.device = ASR_DEVICE
        self.model = None

        logger.info(f"🚀 Initializing ASR Engine: {self.model_type}")
        self._init_whisper()

    def _init_whisper(self):
        """
        Loads the Whisper model using faster-whisper (CTranslate2).
        Uses int8 quantization for high-speed CPU inference.
        """
        try:
            logger.info(f"Loading Whisper from: {ASR_MODEL_PATH}")
            # int8 quantization is perfect for your Acer Aspire's CPU
            self.model = WhisperModel(
                ASR_MODEL_PATH, 
                device=self.device, 
                compute_type="int8"
            )
            logger.info("✅ Whisper-base Loaded Successfully (Stable Mode).")
        except Exception as e:
            logger.critical(f"❌ Failed to load Whisper: {e}")
            raise e

    def transcribe(self, audio_bytes):
        """
        Transcribes raw 16kHz PCM bytes from the UDP stream.
        """
        if not audio_bytes or len(audio_bytes) < 3200:
            return "", 0.0, {"stt_lat_ms": 0}

        start_time = time.perf_counter()
        
        try:
            # 1. Normalize the raw 16-bit PCM bytes into float32
            # Equation: $x_{normalized} = \frac{x_{pcm}}{32768.0}$
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            # 2. Transcribe via Faster-Whisper
            # segments is a generator; we iterate to get the full text
            segments, info = self.model.transcribe(
                audio_np, 
                beam_size=5, 
                language="en",
                initial_prompt=WHISPER_PROMPT
            )

            full_text = []
            avg_logprob = 0.0
            count = 0

            for segment in segments:
                full_text.append(segment.text)
                avg_logprob += segment.avg_logprob
                count += 1

            # 3. Finalize and Telemetry
            text = " ".join(full_text).strip()
            confidence = np.exp(avg_logprob / count) if count > 0 else 0.0
            
            latency_ms = (time.perf_counter() - start_time) * 1000
            telemetry = {"stt_lat_ms": round(latency_ms, 2)}

            if text:
                logger.info(f"🗣️ [WHISPER] '{text}' ({latency_ms:.1f}ms)")
            
            return text, confidence, telemetry

        except Exception as e:
            logger.error(f"Whisper Inference Error: {e}")
            return "", 0.0, {"stt_lat_ms": 0}