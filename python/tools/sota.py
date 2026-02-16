import os
import logging
from huggingface_hub import snapshot_download

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("SemanticDownloader")

# === PATH CONFIGURATION ===
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__)) 
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets", "models")

# Define target folders
BGE_M3_PATH = os.path.join(ASSETS_DIR, "bge-m3")
RERANKER_PATH = os.path.join(ASSETS_DIR, "bge-reranker-v2-m3")

# Create folders
os.makedirs(BGE_M3_PATH, exist_ok=True)
os.makedirs(RERANKER_PATH, exist_ok=True)

# === MIRROR CONFIG ===
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

logger.info(">>> STARTING SEMANTIC BRAIN UPGRADE DOWNLOADER <<<")

# --- 1. DOWNLOAD BGE-M3 (The Contextual Brain) ---
try:
    logger.info("1/2: Downloading BGE-M3 (SOTA Embeddings)...")
    snapshot_download(
        repo_id="BAAI/bge-m3",
        local_dir=BGE_M3_PATH,
        local_dir_use_symlinks=False,
        ignore_patterns=["*.msgpack", "*.h5", "*.ot"] 
    )
    logger.info("✅ BGE-M3 Secured.")
except Exception as e:
    logger.error(f"❌ BGE-M3 Download Failed: {e}")

# --- 2. DOWNLOAD BGE-RERANKER (The Quality Filter) ---
try:
    logger.info("2/2: Downloading BGE-Reranker-V2-M3...")
    snapshot_download(
        repo_id="BAAI/bge-reranker-v2-m3",
        local_dir=RERANKER_PATH,
        local_dir_use_symlinks=False,
        ignore_patterns=["*.msgpack", "*.h5", "*.ot"]
    )
    logger.info("✅ BGE-Reranker Secured.")
except Exception as e:
    logger.error(f"❌ Reranker Download Failed: {e}")

print("\n---------------------------------------------------")
print("🧠 SEMANTIC UPGRADE COMPLETE.")
print(f"Models saved to: {ASSETS_DIR}")
print("---------------------------------------------------")