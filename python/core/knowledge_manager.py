import os
import chromadb
import numpy as np
import logging
from sentence_transformers import SentenceTransformer, CrossEncoder

logger = logging.getLogger("Librarian")

class KnowledgeManager:
    def __init__(self, shared_scout=None):
        self.project_root = "/media/nishchay/Study/syntheta-hub"
        self.db_path = os.path.join(self.project_root, "assets/database")
        
        # 🟢 THE SCOUT: all-MiniLM-L6-v2 (Already in RAM for SemanticBrain)
        # We reuse this for instant first-pass retrieval
        if shared_scout:
            self.scout = shared_scout
        else:
            scout_path = os.path.join(self.project_root, "assets/models/all-MiniLM-L6-v2")
            self.scout = SentenceTransformer(scout_path, device="cpu")
        
        # 🔴 THE JUDGE: BGE-Reranker (Only processes top 10 results)
        ranker_path = os.path.join(self.project_root, "assets/models/bge-reranker-v2-m3")
        self.judge = CrossEncoder(ranker_path, device="cpu")

        # 📂 Connection to ChromaDB
        self.client = chromadb.PersistentClient(path=self.db_path)
        self.collection = self.client.get_collection(name="syntheta_docs")
        logger.info("⚡ OMEGA Knowledge Engine Optimized (MiniLM Scout + BGE Judge).")

    def get_context(self, query, top_k=15, rerank_k=3):
        """Finds context in < 2 seconds using a two-stage pipeline."""
        
        # 1️⃣ STAGE 1: Scout (Vector Search) - Takes ~50-100ms
        query_vec = self.scout.encode(query, normalize_embeddings=True).tolist()
        results = self.collection.query(
            query_embeddings=[query_vec],
            n_results=top_k
        )

        if not results['documents'] or not results['documents'][0]:
            return ""

        documents = results['documents'][0]
        metadatas = results['metadatas'][0]

        # 2️⃣ STAGE 2: Judge (Reranking) - Takes ~500-800ms
        # We only feed the top 15 results to the heavy reranker
        pairs = [[query, doc] for doc in documents]
        scores = self.judge.predict(pairs)
        
        # Sort by reranker scores (Highest first)
        ranked_indices = np.argsort(scores)[::-1]
        
        # 3️⃣ STAGE 3: Context Assembly
        context_blocks = []
        for i in ranked_indices[:rerank_k]:
            path = metadatas[i]['path']
            content = documents[i]
            context_blocks.append(f"--- FILE: {path} ---\n{content}\n")

        return "\n".join(context_blocks)