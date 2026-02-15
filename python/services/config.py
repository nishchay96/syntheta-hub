import os
import logging

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s', 
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("Syntheta")

# ==================== PATHS (RELATIVE) ====================
# 🔧 FIX: Adjusted depth for 'python/services/config.py'
# python/services/ -> python/ -> syntheta-hub/
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))

TEMP_DIR = os.path.join(BASE_DIR, "assets", "temp")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Ensure directories exist
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ==================== AUDIO SETTINGS ====================
# Forced Offline Mode
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# Audio Processing
AGC_TARGET = 0.03
TRANSCRIPTION_BOOST = 2.0
SILENCE_TIMEOUT = 2.0

# 🔧 NOTE: 1280 bytes = 640 samples = 40ms of audio (at 16kHz)
# This is a good size for VAD processing but small for buffering.
# The Engine handles accumulation, so this is safe.
TARGET_CHUNK_SIZE = 1280 

# ==================== INTEGRATIONS ====================
ENABLE_SERVER_WAKE_CHECK = False
WAKE_MODELS = []
MODEL_SIZE = "base.en"
WHISPER_PROMPT = "Syntheta system command. Turn on light. Stop. What time is it."

# Home Assistant
HA_URL = "http://localhost:8123/api/services"
HA_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI4ODdkNDVlOTY5NGI0YTcxOGY4MDdkMjM3MjQwNjdhNCIsImlhdCI6MTc2ODI0MzY3MywiZXhwIjoyMDgzNjAzNjczfQ.-rhWgtHL2tfDRCWSduQftVRaoP73tHNiC_VDIIN91nQ"