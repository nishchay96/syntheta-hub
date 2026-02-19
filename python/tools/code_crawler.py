import os
import chromadb
import logging
import subprocess
import re
from sentence_transformers import SentenceTransformer

# ==================== ✅ CONFIGURATION ====================
PROJECT_ROOT = "/media/nishchay/Study/syntheta-hub"
DB_PATH = os.path.join(PROJECT_ROOT, "assets/database")
MODEL_PATH = os.path.join(PROJECT_ROOT, "assets/models/bge-m3")

EXTENSIONS = {'.go', '.py', '.json', '.md', '.bat', '.sh', '.txt', '.c', '.h'}
IGNORE_DIRS = {'.git', 'venv', 'venv-audio', '__pycache__', 'assets', 'database', 'build'}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Librarian-Crawler")

# ==================== 🛠️ GIT UTILS ====================
def get_changed_files():
    """Uses git to find modified or untracked files."""
    try:
        # Get modified files (tracked) and untracked files
        cmd = ["git", "ls-files", "-m", "-o", "--exclude-standard"]
        output = subprocess.check_output(cmd, cwd=PROJECT_ROOT).decode('utf-8')
        files = output.splitlines()
        return [f for f in files if any(f.endswith(ext) for ext in EXTENSIONS)]
    except Exception as e:
        logger.error(f"Git check failed: {e}. Falling back to full scan.")
        return None

# ==================== 🧠 CHUNKING LOGIC ====================
def chunk_code(text, file_path, max_chars=1500):
    if file_path.endswith(('.py', '.go', '.c', '.h')):
        pattern = r'(?m)^(?:def\s+|func\s+|void\s+|int\s+|static\s+|class\s+)'
        parts = re.split(pattern, text)
        chunks = []
        current_chunk = parts[0]
        for part in parts[1:]:
            if len(current_chunk) + len(part) < max_chars:
                current_chunk += part
            else:
                chunks.append(current_chunk)
                current_chunk = part
        chunks.append(current_chunk)
        return [c for c in chunks if len(c.strip()) > 20]
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars - 200)]

# ==================== 🚀 INCREMENTAL EXECUTION ====================
def run_ingestion():
    os.environ["HF_HUB_OFFLINE"] = "1"
    
    # 1. Identify what to scan
    targets = get_changed_files()
    
    if targets is not None and len(targets) == 0:
        logger.info("✨ Git reports no changes. OMEGA Memory is already up to date.")
        return

    # 2. Setup DB and Model (only if we actually have work to do)
    logger.info(f"📚 Loading BGE-M3 for indexing {len(targets) if targets else 'all'} files...")
    model = SentenceTransformer(MODEL_PATH, device="cpu")
    client = chromadb.PersistentClient(path=DB_PATH)
    collection = client.get_or_create_collection(name="syntheta_docs")

    # 3. Process Targets
    if targets is None:
        # Full scan fallback
        it_files = []
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for f in files:
                if any(f.endswith(ext) for ext in EXTENSIONS):
                    it_files.append(os.path.relpath(os.path.join(root, f), PROJECT_ROOT))
        targets = it_files

    for rel_path in targets:
        file_path = os.path.join(PROJECT_ROOT, rel_path)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            chunks = chunk_code(content, rel_path)
            
            # Delete old chunks for this file first to avoid duplicates
            collection.delete(where={"path": rel_path})
            
            for i, chunk in enumerate(chunks):
                chunk_id = f"{rel_path}_chunk_{i}"
                embedding = model.encode(chunk, normalize_embeddings=True).tolist()
                collection.upsert(
                    ids=[chunk_id],
                    embeddings=[embedding],
                    metadatas=[{"path": rel_path, "chunk_idx": i}],
                    documents=[chunk]
                )
            logger.info(f"✅ Re-indexed: {rel_path}")
        except Exception as e:
            logger.error(f"⚠️ Failed: {rel_path}: {e}")

if __name__ == "__main__":
    run_ingestion()