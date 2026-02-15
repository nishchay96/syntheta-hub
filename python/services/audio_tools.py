import os
import wave
import time
import logging

# Ensure this matches your directory structure
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
TEMP_DIR = os.path.join(BASE_DIR, "assets", "temp")

logger = logging.getLogger("AudioTools")

def cleanup_old_files(max_age_seconds=300):
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

def create_resume_file(original_path, seconds_played, rewind_sec=2.0):
    """
    Creates a temporary WAV file starting from (played - rewind).
    """
    if not original_path or not os.path.exists(original_path):
        logger.warning(f"Resume failed: Source file missing '{original_path}'")
        return None
    
    cleanup_old_files()

    filename = f"resume_{int(time.time())}.wav"
    output_path = os.path.join(TEMP_DIR, filename)
    
    try:
        with wave.open(original_path, 'rb') as src:
            nchannels, sampwidth, framerate, nframes, comptype, compname = src.getparams()
            
            # 1. Calculate Frame Offset
            resume_time = max(0, seconds_played - rewind_sec)
            start_frame = int(resume_time * framerate)
            
            # 2. Safety Check
            if start_frame >= nframes:
                logger.info("Resume point is at/after end of file. Nothing to resume.")
                return None
            
            # 3. Seek & Read remaining
            src.setpos(start_frame)
            frames_to_read = nframes - start_frame
            data = src.readframes(frames_to_read)
            
            # 4. Write Segment (Avoids setparams size trap)
            with wave.open(output_path, 'wb') as dst:
                dst.setnchannels(nchannels)
                dst.setsampwidth(sampwidth)
                dst.setframerate(framerate)
                dst.writeframes(data)
                
        # Pad the newly created resume file
        pad_audio_file(output_path, 150)
        
        logger.info(f"✅ Resume file created: {filename} (Starts at {resume_time:.2f}s)")
        return output_path

    except Exception as e:
        logger.error(f"Resume Slicer Failed: {e}. Falling back to original.")
        return original_path