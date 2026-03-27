import os
import logging

# ==================== CONFIGURATION LOADING ====================
def load_env_file(dotenv_path):
    """Simple manual .env loader for systems without python-dotenv."""
    if os.path.exists(dotenv_path):
        with open(dotenv_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())

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
LOG_DIR  = os.path.join(BASE_DIR, "logs")

# Load environment variables from .env if it exists
load_env_file(os.path.join(BASE_DIR, ".env"))

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR,  exist_ok=True)

# ==================== KNOWLEDGE VAULT ====================
# Replaces ~/.rowboat/knowledge — now lives inside the project
KNOWLEDGE_VAULT_PATH = os.path.join(BASE_DIR, "assets", "knowledge")
os.makedirs(KNOWLEDGE_VAULT_PATH, exist_ok=True)

# ==================== AUDIO SETTINGS ====================
# Force offline modes (respect existing env vars if any, else default to 1)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

AGC_TARGET          = 0.03
TRANSCRIPTION_BOOST = 2.0
SILENCE_TIMEOUT     = 2.0
TARGET_CHUNK_SIZE   = 1280

# ==================== ASR SETTINGS ====================
ASR_MODEL_TYPE = "WHISPER"
MODEL_SIZE     = "base.en"
ASR_MODEL_PATH = os.path.join(BASE_DIR, "assets", "models", "whisper-base-en")
ASR_DEVICE     = "cpu"
WHISPER_PROMPT = "Syntheta system command. Turn on light. Stop. What time is it."

# ==================== TTS CONFIGURATION (KOKORO) ====================
KOKORO_VOICE_PRIMARY   = 'bf_emma'
KOKORO_VOICE_SECONDARY = 'af_bella'
KOKORO_SPEED           = 1.1

# ==================== TELEMETRY & DEBUG ====================
ENABLE_LATENCY_TELEMETRY = True
ENABLE_SERVER_WAKE_CHECK = False

# ==================== INTEGRATIONS ====================
WAKE_MODELS = []

# ==================== GLOBAL LIVE CACHE DEFAULTS ====================
GLOBAL_WEATHER_CITY = os.getenv("GLOBAL_WEATHER_CITY", "Guwahati")

# Home Assistant
HA_URL   = os.getenv("HA_URL", "http://localhost:8123/api/services")
HA_TOKEN = os.getenv("HA_TOKEN", "")
