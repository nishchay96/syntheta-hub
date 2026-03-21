import os
import json
import logging
import urllib.request
import numpy as np
import requests

logger = logging.getLogger("APIScout")

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
NOMIC_MODEL = "nomic-embed-text:v1.5"
LLM_MODEL = "llama3.2:3b"

class APIScout:
    def __init__(self):
        self.catalog_path = os.path.join(os.path.dirname(__file__), "api_catalog.json")
        self.apis = []
        self.vectors = []
        self._load_catalog()

    def _embed(self, text: str) -> np.ndarray:
        try:
            payload = {
                "model": NOMIC_MODEL,
                "prompt": f"search_document: {text}",
                "keep_alive": -1
            }
            req = urllib.request.Request(
                OLLAMA_EMBED_URL,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=5.0) as res:
                return np.array(json.loads(res.read().decode('utf-8'))['embedding'])
        except Exception as e:
            logger.error(f"⚠️ APIScout Nomic embed failed: {e}")
            return None

    def _load_catalog(self):
        try:
            if not os.path.exists(self.catalog_path):
                logger.warning(f"⚠️ API Catalog not found at {self.catalog_path}")
                return
            with open(self.catalog_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.apis = data.get("apis", [])
            for api in self.apis:
                desc = api.get("Description", "")
                vec = self._embed(desc)
                self.vectors.append(vec)
            logger.info(f"🌐 API Scout Loaded {len(self.apis)} APIs.")
        except Exception as e:
            logger.error(f"❌ Failed to load API Catalog: {e}")

    def lookup_api(self, text: str, query_vector: np.ndarray = None, threshold: float = 0.85):
        if not self.apis or not self.vectors:
            return None

        if query_vector is None:
            query_vector = self._embed(text)
            if query_vector is None:
                return None

        q_vec = np.array(query_vector, dtype=np.float32)
        scored = []
        for i, v in enumerate(self.vectors):
            if v is None: continue
            mem_vec = np.array(v, dtype=np.float32)
            denom = np.linalg.norm(q_vec) * np.linalg.norm(mem_vec)
            sim = float(np.dot(q_vec, mem_vec) / denom) if denom > 0 else 0.0
            scored.append((sim, self.apis[i]))

        scored.sort(reverse=True, key=lambda x: x[0])
        top_3 = scored[:3]
        
        # Fast path rejection
        if not top_3 or top_3[0][0] < 0.5:
            return None

        # Filter top 3 string for LLM
        tools_desc = ""
        for idx, (sim, api) in enumerate(top_3):
            tools_desc += f"Tool {idx + 1}: {api['API_ID']} - {api['Description']}\n"

        prompt = (
            f"You are a tool dispatcher. Can any of the following tools answer the user query?\n"
            f"{tools_desc}\n"
            f"User Query: '{text}'\n"
            f"If none of them match perfectly, reply with 'NONE'. Otherwise, reply with ONLY the Tool ID (e.g., MOVIEDB_SEARCH).\n"
            f"No explanation, no extra text."
        )

        payload = {
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 10}
        }

        try:
            res = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=5.0)
            llm_result = res.json().get("message", {}).get("content", "").strip().upper()
            
            if llm_result == "NONE" or "NONE" in llm_result:
                return None

            for sim, api in top_3:
                if api['API_ID'] in llm_result:
                    if sim >= threshold:
                        logger.info(f"🎯 API Scout Match: {api['API_ID']} (sim: {sim:.3f})")
                        # Emulate API response logic (placeholder since actual APIs need keys) #
                        return f"[API SCOUT HIT: {api['API_ID']}] We would call {api['Endpoint']} here for '{text}'."
        except Exception as e:
            logger.error(f"❌ LLM API Dispatch failed: {e}")

        return None
