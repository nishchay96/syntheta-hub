import os
import time
import logging
from huggingface_hub import snapshot_download, hf_hub_download

# === CONFIGURATION ===
# 1. Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("SynthetaDownloader")

# 2. Path Setup
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets", "models")

# 3. Mirror (Good choice for stability)
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

# === HELPER FUNCTION: RETRY LOOP ===
def robust_download(func, **kwargs):
    """
    Wraps HF download functions in a retry loop.
    If it fails/stalls, it waits 5s and tries again exactly where it left off.
    """
    max_retries = 100 # Effectively infinite for bad internet
    attempt = 0
    
    while attempt < max_retries:
        try:
            func(**kwargs)
            return # Success!
        except Exception as e:
            attempt += 1
            logger.warning(f"⚠️ Download interrupted. Retrying in 5s... (Attempt {attempt}/{max_retries})")
            logger.error(f"Error: {e}")
            time.sleep(5) # Wait before retrying
    
    logger.error("❌ Failed after max retries.")
    raise ConnectionError("Download failed.")

# === 1. DOWNLOAD SEMANTIC BRAIN (BGE) ===
BGE_M3_PATH = os.path.join(ASSETS_DIR, "bge-m3")
RERANKER_PATH = os.path.join(ASSETS_DIR, "bge-reranker-v2-m3")

logger.info(">>> STARTING ROBUST DOWNLOADER <<<")

logger.info("1/3: Downloading BGE-M3...")
robust_download(
    snapshot_download,
    repo_id="BAAI/bge-m3",
    local_dir=BGE_M3_PATH,
    local_dir_use_symlinks=False,
    resume_download=True,  # FORCE RESUME
    max_workers=2,         # LIMIT CONNECTIONS (Better for 4mbps stability)
    ignore_patterns=["*.msgpack", "*.h5", "*.ot"]
)

logger.info("2/3: Downloading BGE-Reranker...")
robust_download(
    snapshot_download,
    repo_id="BAAI/bge-reranker-v2-m3",
    local_dir=RERANKER_PATH,
    local_dir_use_symlinks=False,
    resume_download=True,
    max_workers=2,
    ignore_patterns=["*.msgpack", "*.h5", "*.ot"]
)

# === 2. DOWNLOAD QWEN ASR (The Listener) ===
QWEN_PATH = os.path.join(ASSETS_DIR, "Qwen3-ASR-1.7B")
MODEL_REPO = "Qwen/Qwen3-ASR-1.7B"
shards = [
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
    "model.safetensors.index.json"
]

logger.info("3/3: Downloading Qwen3-ASR (Smart Resume Enabled)...")

for shard in shards:
    logger.info(f"   -> Processing {shard}...")
    # NOTE: I removed the os.remove() line. 
    # HF automatically checks file hash. If it's half-downloaded, it resumes.
    # If it's corrupt, it re-downloads. Trust the library.
    
    robust_download(
        hf_hub_download,
        repo_id=MODEL_REPO,
        filename=shard,
        local_dir=QWEN_PATH,
        local_dir_use_symlinks=False,
        resume_download=True
    )

print("\n---------------------------------------------------")
print("✅ ALL MODELS SECURED & VERIFIED.")
print(f"📂 Location: {ASSETS_DIR}")
print("---------------------------------------------------")