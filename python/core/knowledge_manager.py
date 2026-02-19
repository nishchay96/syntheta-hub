import os
import chromadb
import numpy as np
import logging
from sentence_transformers import SentenceTransformer, CrossEncoder

logger = logging.getLogger("Librarian")

class KnowledgeManager:
    def __init__(self):
        self.project_root = "/media/nishchay/Study/syntheta-hub"
        self.db_path = os.path.join(self.project_root, "assets/database")
        self.m3_path = os.path.join(self.project_root, "assets/models/bge-m3")
        self.ranker_path = os.path.join(self.project_root, "assets/models/bge-reranker-v2-m3")

        # 1. The Librarian (Embeddings)
        self.librarian = SentenceTransformer(self.m3_path, device="cpu")
        
        # 2. The Judge (Reranker)
        self.judge = CrossEncoder(self.ranker_path, device="cpu")

        # 3. Connection to ChromaDB
        self.client = chromadb.PersistentClient(path=self.db_path)
        self.collection = self.client.get_collection(name="syntheta_docs")
        logger.info("✅ OMEGA Knowledge Engine Online.")

    def get_context(self, query, top_k=10, rerank_k=3):
        """Finds the best code snippets for the query."""
        # Step 1: Vector Search (Librarian)
        query_vec = self.librarian.encode(query, normalize_embeddings=True).tolist()
        results = self.collection.query(
            query_embeddings=[query_vec],
            n_results=top_k
        )

        if not results['documents']:
            return ""

        documents = results['documents'][0]
        metadatas = results['metadatas'][0]

        # Step 2: Reranking (The Judge)
        # Create pairs: [query, document]
        pairs = [[query, doc] for doc in documents]
        scores = self.judge.predict(pairs)
        
        # Sort by reranker scores
        ranked_indices = np.argsort(scores)[::-1]
        
        # Step 3: Build the Best Context
        context_blocks = []
        for i in ranked_indices[:rerank_k]:
            path = metadatas[i]['path']
            content = documents[i]
            context_blocks.append(f"--- FILE: {path} ---\n{content}\n")

        return "\n".join(context_blocks)