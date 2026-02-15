import numpy as np
import logging

class AudioGatekeeper:
    def __init__(self):
        # 🟢 MULTI-NODE STATE: Maps sat_id -> Room Calibration Threshold
        self.calibrated_thresholds = {}  
        self.default_threshold = 300  
        self.logger = logging.getLogger("Gatekeeper")
        self.logger.info("🛡️ Gatekeeper initialized. Operating in Two-Phase Session Validation mode.")

    def update_calibration(self, sat_id, floor):
        """
        Phase 1 Baseline: Updates the filter based on Alpha's real-world room noise.
        """
        # Logic Block 1: Trust Alpha's Floor + Margin
        margin = 300 
        self.calibrated_thresholds[sat_id] = int(floor) + margin
        self.logger.info(f"🛡️ CALIBRATION UPDATED [Sat {sat_id}] | Floor: {floor} | New Baseline: {self.calibrated_thresholds[sat_id]}")

    # 🟢 DELETED: set_dynamic_range()
    # Omega no longer uses dynamic WWD energy. It trusts Alpha for Phase 2 extension.

    def is_speech(self, sat_id, full_audio_buffer):
        """
        Phase 1 Validator: Evaluates the entire initial audio stream from Alpha.
        Uses ONLY the room calibration to detect false wakes.
        """
        if not full_audio_buffer: return False
        
        # Fetch specific room threshold, fallback to default if unknown
        threshold = self.calibrated_thresholds.get(sat_id, self.default_threshold)
        
        try:
            # 🟢 FUTURE-PROOF MATH: Replace deprecated audioop with fast NumPy array math
            audio_data = np.frombuffer(full_audio_buffer, dtype=np.int16)
            
            if audio_data.size == 0:
                return False
                
            # Calculate Root Mean Square (Loudness) using float32 to prevent overflow
            rms = np.sqrt(np.mean(np.square(audio_data.astype(np.float32))))
            
            self.logger.debug(f"[Sat {sat_id}] Session RMS: {rms:.1f} | Required Baseline: {threshold}")
            
            if rms > threshold:
                return True  # Valid human command
            return False     # False wake (TV, AC, door slam)
            
        except Exception as e:
            # Fail open (allow audio) if math fails to avoid deafening the bot
            self.logger.error(f"RMS Calculation Error on Sat {sat_id}: {e}")
            return True