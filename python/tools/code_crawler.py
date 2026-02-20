import os
import chromadb
import logging
import subprocess
import re
from sentence_transformers import SentenceTransformer

# ==================== ✅ CONFIGURATION ====================
PROJECT_ROOT = "/media/nishchay/Study/syntheta-hub"
DB_PATH = os.path.join(PROJECT_ROOT, "assets/database")
# 🟢 Changed to MiniLM to match the new KnowledgeManager Scout
MODEL_PATH = os.path.join(PROJECT_ROOT, "assets/models/all-MiniLM-L6-v2")

EXTENSIONS = {'.go', '.py', '.json', '.md', '.bat', '.sh', '.txt', '.c', '.h'}
IGNORE_DIRS = {'.git', 'venv', 'venv-audio', '__pycache__', 'assets', 'database', 'build'}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Librarian-Crawler")

def get_changed_files():
    try:
        cmd = ["git", "ls-files", "-m", "-o", "--exclude-standard"]
        output = subprocess.check_output(cmd, cwd=PROJECT_ROOT).decode('utf-8')
        files = output.splitlines()
        return [f for f in files if any(f.endswith(ext) for ext in EXTENSIONS)]
    except:
        return None

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

def run_ingestion():
    os.environ["HF_HUB_OFFLINE"] = "1"
    
    # 1. Connect to DB
    client = chromadb.PersistentClient(path=DB_PATH)
    
    # 🟢 DIMENSION SAFETY: If we're switching models, we must wipe the old data
    try:
        collection = client.get_collection(name="syntheta_docs")
        # Quick test: check if existing data is the wrong dimension
        test_item = collection.get(limit=1, include=['embeddings'])
        if test_item['embeddings'] and len(test_item['embeddings'][0]) != 384:
            logger.warning("⚠️ Dimension mismatch detected (1024 vs 384). Wiping collection...")
            client.delete_collection("syntheta_docs")
            collection = client.create_collection(name="syntheta_docs")
    except:
        collection = client.get_or_create_collection(name="syntheta_docs")

    # 2. Check for changes
    targets = get_changed_files()
    if targets is not None and len(targets) == 0:
        logger.info("✨ OMEGA Memory is already up to date.")
        return

    # 3. Load Model & Index
    logger.info(f"📚 Loading MiniLM (384-dim) for indexing...")
    model = SentenceTransformer(MODEL_PATH, device="cpu")

    if targets is None:
        targets = []
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for f in files:
                if any(f.endswith(ext) for ext in EXTENSIONS):
                    targets.append(os.path.relpath(os.path.join(root, f), PROJECT_ROOT))

    for rel_path in targets:
        file_path = os.path.join(PROJECT_ROOT, rel_path)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            chunks = chunk_code(content, rel_path)
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
            logger.info(f"✅ Indexed: {rel_path}")
        except Exception as e:
            logger.error(f"⚠️ Failed: {rel_path}: {e}")

if __name__ == "__main__":
    run_ingestion()