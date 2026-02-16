import logging
import os
import torch
import numpy as np
from qwen_asr import Qwen3ASRModel

# CONFIG
MODEL_FOLDER_NAME = "Qwen3-ASR-1.7B"
logger = logging.getLogger("QwenASR")

class AudioTranscriber:
    def __init__(self, model_size=None, device="cuda"):
        """
        🚀 MEMORY OPTIMIZED: Qwen3-ASR for 4GB GPUs
        Uses float16 and automatic device mapping.
        """
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
        model_path = os.path.join(base_dir, "assets", "models", MODEL_FOLDER_NAME)

        logger.info(f"Initializing Qwen3-ASR (Memory Optimized Mode)...")

        try:
            # 🟢 THE FIX: 
            # 1. dtype=torch.float16 (Uses 2x less VRAM than float32)
            # 2. device_map="auto" (Spills over to CPU RAM if 4GB isn't enough)
            # 3. low_cpu_mem_usage=True (Prevents RAM spikes during loading)
            self.model = Qwen3ASRModel.from_pretrained(
                model_path,
                dtype=torch.float16 if self.device == "cuda" else torch.float32,
                device_map="auto", 
                trust_remote_code=True,
                low_cpu_mem_usage=True
            )
            logger.info("✅ Qwen3-ASR Loaded. Memory balanced between GPU/CPU.")
            
        except Exception as e:
            logger.critical(f"Failed to load Qwen3-ASR: {e}")
            raise e

    def transcribe(self, audio_bytes):
        if not audio_bytes or len(audio_bytes) < 3200: 
            return "", 0.0

        try:
            # Convert raw PCM16 bytes to Float32 array
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            # Native Inference
            results = self.model.transcribe(
                audio=(audio_np, 16000), 
                language="English"
            )

            if results and len(results) > 0:
                return results[0].text.strip(), 1.0
            
            return "", 0.0

        except Exception as e:
            logger.error(f"Transcription Error: {e}")
            return "", 0.0