import os
import chromadb
from chromadb.utils import embedding_functions
from sentence_transformers import SentenceTransformer

# --- CONFIGURATION ---
PROJECT_ROOT = "/media/nishchay/Study/syntheta-hub"
DB_PATH = os.path.join(PROJECT_ROOT, "assets/database")
MODEL_PATH = "/media/nishchay/Study/syntheta-hub/assets/models/bge-m3"

# File extensions we care about
EXTENSIONS = {'.go', '.py', '.json', '.md', '.bat', '.sh', '.txt'}
# Directories to ignore
IGNORE_DIRS = {'.git', 'venv', 'venv-audio', '__pycache__', 'assets', 'database'}

print("📚 Initializing OMEGA Librarian (BGE-M3)...")
# Load the model for embedding
model = SentenceTransformer(MODEL_PATH, device="cpu")

# Initialize ChromaDB (Persistent Storage)
client = chromadb.PersistentClient(path=DB_PATH)
collection = client.get_or_create_collection(name="syntheta_docs")

def chunk_text(text, size=500, overlap=50):
    """Simple sliding window chunking for code and docs."""
    chunks = []
    for i in range(0, len(text), size - overlap):
        chunks.append(text[i:i + size])
    return chunks

print("📂 Crawling project files...")
indexed_count = 0

for root, dirs, files in os.walk(PROJECT_ROOT):
    # Skip ignored directories
    dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
    
    for file in files:
        if any(file.endswith(ext) for ext in EXTENSIONS):
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, PROJECT_ROOT)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Create chunks to handle long files
                chunks = chunk_text(content)
                
                for i, chunk in enumerate(chunks):
                    chunk_id = f"{rel_path}_chunk_{i}"
                    
                    # Generate vector using BGE-M3
                    embedding = model.encode(chunk, normalize_embeddings=True).tolist()
                    
                    # Store in Vector DB
                    collection.upsert(
                        ids=[chunk_id],
                        embeddings=[embedding],
                        metadatas=[{"path": rel_path, "chunk": i}],
                        documents=[chunk]
                    )
                
                indexed_count += 1
                print(f"✅ Indexed: {rel_path} ({len(chunks)} chunks)")
                
            except Exception as e:
                print(f"⚠️  Skipped {rel_path}: {e}")

print(f"\n✨ SUCCESS! {indexed_count} files indexed into OMEGA Memory.")