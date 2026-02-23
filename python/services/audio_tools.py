import os
import wave
import time
import logging

# Ensure this matches your directory structure
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
TEMP_DIR = os.path.join(BASE_DIR, "assets", "temp")

logger = logging.getLogger("AudioTools")

# 🟢 FIX: Extended TTL from 300s (5m) to 3600s (1h) to prevent "Source file missing" errors
def cleanup_old_files(max_age_seconds=3600):
    """Cron-job style cleaner. Deletes WAV files older than max_age_seconds."""
    try:
        if not os.path.exists(TEMP_DIR): 
            os.makedirs(TEMP_DIR, exist_ok=True)
            return
            
        now = time.time()
        for filename in os.listdir(TEMP_DIR):
            if not filename.endswith(".wav") and not filename.endswith(".tmp"): 
                continue
            
            filepath = os.path.join(TEMP_DIR, filename)
            try:
                # 🟢 LOGIC: Files are now persisted for 1 hour to allow for slow user confirmations
                if os.path.getmtime(filepath) < (now - max_age_seconds):
                    os.remove(filepath)
            except OSError:
                pass 
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")

def pad_audio_file(file_path, silence_ms=150):
    """
    Adds silence to Start and End of audio.
    Uses atomic temp-file replacement to prevent corruption.
    """
    if not os.path.exists(file_path): return file_path

    temp_path = file_path + ".tmp"

    try:
        # 1. Read Original
        with wave.open(file_path, 'rb') as src:
            nchannels, sampwidth, framerate, nframes, comptype, compname = src.getparams()
            frames = src.readframes(nframes)

        # 2. Generate Mathematically Perfect Silence Buffer
        silence_frames = int(framerate * (silence_ms / 1000.0))
        block_align = nchannels * sampwidth
        silence_data = b'\x00' * (silence_frames * block_align)

        # 3. Write New File Atomically (Avoids setparams size trap)
        with wave.open(temp_path, 'wb') as dst:
            dst.setnchannels(nchannels)
            dst.setsampwidth(sampwidth)
            dst.setframerate(framerate)
            
            dst.writeframes(silence_data) # Pre-roll
            dst.writeframes(frames)       # Content
            dst.writeframes(silence_data) # Post-roll
            
        # 4. Swap files cleanly
        os.replace(temp_path, file_path)
        return file_path
        
    except Exception as e:
        logger.error(f"Padding Failed: {e}")
        # Clean up the dead temp file if it failed
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return file_path