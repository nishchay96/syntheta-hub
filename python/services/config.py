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
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
TEMP_DIR = os.path.join(BASE_DIR, "assets", "temp")
LOG_DIR = os.path.join(BASE_DIR, "logs")

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ==================== AUDIO SETTINGS ====================
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

AGC_TARGET = 0.03
TRANSCRIPTION_BOOST = 2.0
SILENCE_TIMEOUT = 2.0
TARGET_CHUNK_SIZE = 1280 

# ==================== ASR SETTINGS ====================
ASR_MODEL_TYPE = "WHISPER"
MODEL_SIZE = "base.en"
ASR_MODEL_PATH = os.path.join(BASE_DIR, "assets", "models", "whisper-base-en")
ASR_DEVICE = "cpu"

WHISPER_PROMPT = "Syntheta system command. Turn on light. Stop. What time is it."

# ==================== TTS CONFIGURATION (KOKORO) ====================
KOKORO_VOICE_PRIMARY = 'bf_emma'
KOKORO_VOICE_SECONDARY = 'af_bella'
KOKORO_SPEED = 1.1

# ==================== TELEMETRY & DEBUG ====================
# 🟢 FIX: Added the missing flag that caused the engine NameError
ENABLE_LATENCY_TELEMETRY = True
ENABLE_SERVER_WAKE_CHECK = False

# ==================== INTEGRATIONS ====================
WAKE_MODELS = []

# Home Assistant
HA_URL = "http://localhost:8123/api/services"
HA_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI4ODdkNDVlOTY5NGI0YTcxOGY4MDdkMjM3MjQwNjdhNCIsImlhdCI6MTc2ODI0MzY3MywiZXhwIjoyMDgzNjAzNjczfQ.-rhWgtHL2tfDRCWSduQftVRaoP73tHNiC_VDIIN91nQ"