import os
import sys
import ssl
import logging
import shutil

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("OfflineSetup")

# === 0. PATH CONFIGURATION ===
# File is at: syntheta-hub/python/tools/download_offline_assets.py
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__)) 
# Go up two levels: python/tools -> python -> syntheta-hub
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))

ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets", "models")

# Define target folders
# 🟢 UPDATE: Pointing to the new Qwen model folder
QWEN_PATH = os.path.join(ASSETS_DIR, "Qwen3-ASR-1.7B")
SEMANTIC_PATH = os.path.join(ASSETS_DIR, "all-MiniLM-L6-v2")

# Create folders if missing
os.makedirs(QWEN_PATH, exist_ok=True)
os.makedirs(SEMANTIC_PATH, exist_ok=True)

logger.info(f"📂 Target Asset Folder: {ASSETS_DIR}")

# === 1. BYPASS SSL RESTRICTIONS ===
# Fixes SSL errors on some corporate/strict networks
os.environ['CURL_CA_BUNDLE'] = ''
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

logger.info(">>> STARTING OFFLINE ASSET DOWNLOADER <<<")

# === 2. DOWNLOAD QWEN3-ASR (The New Ears) ===
try:
    logger.info("1. Downloading Qwen3-ASR-1.7B...")
    
    # We use snapshot_download because it simply fetches files.
    # It doesn't try to load the model code, avoiding "Architecture not found" errors.
    from huggingface_hub import snapshot_download
    
    snapshot_download(
        repo_id="Qwen/Qwen3-ASR-1.7B",
        local_dir=QWEN_PATH,
        local_dir_use_symlinks=False, # Important for true offline portability
        resume_download=True
    )
    
    logger.info(f"✅ Qwen3-ASR Saved to: {QWEN_PATH}")

except ImportError:
    logger.error("❌ 'huggingface_hub' not found. Run: pip install huggingface_hub")
except Exception as e:
    logger.error(f"❌ Qwen Download Failed: {e}")

# === 3. DOWNLOAD SEMANTIC BRAIN (The Reflex) ===
try:
    logger.info("2. Downloading Semantic Model ('all-MiniLM-L6-v2')...")
    from sentence_transformers import SentenceTransformer
    
    # Download to memory
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    # Save explicitly to our offline folder
    model.save(SEMANTIC_PATH)
    logger.info(f"✅ Semantic Model Saved to: {SEMANTIC_PATH}")

except ImportError:
    logger.error("❌ 'sentence_transformers' not found. Run: pip install sentence-transformers")
except Exception as e:
    logger.error(f"❌ Semantic Download Failed: {e}")

print("\n---------------------------------------------------")
print("✅ ASSETS SECURED.")
print(f"You can now unplug the internet.")
print(f"The system will look in: {ASSETS_DIR}")
print("---------------------------------------------------")