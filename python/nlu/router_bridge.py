import logging
import os
import json
import requests
import urllib.request
import numpy as np
import time
import threading
import glob
import re
from typing import Optional
from datetime import datetime

logger = logging.getLogger("LibrarianRouter")

# ============================================================
# CONFIGURATION
# ============================================================
ROUTER_MODEL    = "llama3.2:3b"   # Routing decisions — already hot in RAM
SEARXNG_URL     = "http://localhost:8080/search"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
NOMIC_MODEL     = "nomic-embed-text:v1.5"
SEARXNG_OFFLINE_COOLDOWN_SEC = 300

# Routing decision thresholds
CLEAR_HIGH = 0.63   # Above → confident, no LLM needed
CLEAR_LOW  = 0.40   # Below → confidently not that type
# Between CLEAR_LOW and CLEAR_HIGH → gray zone → mistral decides

# ============================================================
# ANCHOR TEXTS
# Pure semantic — no keywords
# ============================================================
DOMAIN_ANCHORS = {
    "scientific":  "explain how something works science facts biology chemistry physics space research theory proof experiment",
    "fictional":   "tell me a story creative writing roleplay movies books novels characters plot",
    "emotional":   "i feel sad anxious lonely stressed need advice support mental health struggling",
    "political":   "government policy election war conflict world leaders nations geopolitics official statement",
    "technical":   "code error programming smart home device hardware terminal debug fix setup",
    "persona":     "who are you what is your name who made you your creator identity syntheta",
    "general":     "explain tell me what is how does definition concept meaning describe",
}

WEB_ANCHORS = {
    "web_current_events": (
        "what is happening right now today current news latest update "
        "breaking news recent events this week this month"
    ),
    "web_live_data": (
        "current price stock market weather temperature forecast "
        "exchange rate live score today result petrol rate"
    ),
    "web_people_facts": (
        "who won who is the current who leads who announced "
        "official statement record holder world ranking result"
    ),
    "web_health_opinion": (
        "is it good for health what do experts say research shows "
        "studies suggest what people think reviews opinions benefits"
    ),
    "web_product_market": (
        "best option available right now what is new released "
        "compare models which one to buy market alternatives specs reviews"
    ),
    "web_recommendation_live": (
        "suggest me recommend a better replacement upgrade alternative "
        "what should i get instead whats better than what to choose"
    ),
}

# ============================================================
# ROUTING PROMPT — used only for gray zone queries
# ============================================================
ROUTING_PROMPT = """You are a query router for a personal AI voice assistant.
Classify this query into exactly ONE category.

CATEGORIES:
- web      : requires live internet data (news, weather, prices, current events, product market comparisons, health research, public opinions)
- memory   : requires the user's personal stored facts (their devices, car, family, job, health conditions, personal preferences)
- both     : requires BOTH live web data AND the user's personal facts (e.g. "find a replacement for my phone" needs what phone they own AND current market options)
- general  : general knowledge the AI already knows from training (science explanations, history, how things work, jokes, concepts)

DECISION RULES:
- "my X" where X is possession/person/preference → memory or both
- "upgrade", "replacement", "better than my X", "should i buy" → both
- Current events, prices, weather, news, election results → web
- Health research ("is X good for health"), public opinion ("what do people think") → web
- How does X work, explain X, tell me about X with no personal angle → general

Reply with ONLY the single word: web / memory / both / general
No explanation. No punctuation.

Query: "{query}"
Category:"""


class LibrarianRouter:
    def __init__(self, vault_path: str = None):
        # Vault path for dynamic personal anchor loading
        # In production this is set from MemoryWorker.vault_path
        self.vault_path = vault_path

        # Pre-cached vectors
        self._domain_vecs   = {}
        self._web_vecs      = {}
        self._personal_vecs = {}  # Dynamic — rebuilt when vault changes
        self._personal_lock = threading.Lock()
        
        # Injected externally by SynthetaEngine
        self.api_scout = None

        # Last vault scan time — used to detect new bucket files
        self._last_vault_scan = 0.0
        self._vault_scan_interval = 30.0  # Re-scan every 30s for new nodes
        self._searxng_dead_until = 0.0
        raw_searxng_urls = os.getenv("SEARXNG_URLS", "").strip()
        if raw_searxng_urls:
            self._searxng_urls = [url.strip() for url in raw_searxng_urls.split(",") if url.strip()]
        else:
            self._searxng_urls = [SEARXNG_URL]

        logger.info("🧠 Pre-computing Nomic v1.5 anchors...")
        self._precompute_static_anchors()
        logger.info("🌐 Librarian Router Online | mistral:7b routing | Parallel Nomic/LLM active")

    def pre_load(self):
        """Warm up the routing model and pin it in VRAM on startup."""
        try:
            from nlu.llm_bridge import UI_MODEL
            if ROUTER_MODEL == UI_MODEL:
                logger.info(f"⏭️ Skipping {ROUTER_MODEL} (Router) pre-load (already handled by LLM).")
                return
        except ImportError:
            pass
            
        logger.info(f"🔥 Hot-loading {ROUTER_MODEL} (Router) into VRAM...")
        try:
            payload = {
                "model": ROUTER_MODEL,
                "messages": [{"role": "user", "content": "Syntheta pre-load ping. Reply with 'READY'."}],
                "stream": False,
                "keep_alive": -1,
                "options": {"num_predict": 5}
            }
            requests.post(OLLAMA_CHAT_URL, json=payload, timeout=20.0)
            logger.info(f"✅ {ROUTER_MODEL} (Router) is now PINNED in VRAM.")
        except Exception as e:
            logger.error(f"❌ Failed to hot-load router model {ROUTER_MODEL}: {e}")

    # ----------------------------------------------------------
    # STARTUP
    # ----------------------------------------------------------
    def _precompute_static_anchors(self):
        """Embeds domain and web anchors at startup. Fast — runs once."""
        t = time.perf_counter()
        for cat, text in DOMAIN_ANCHORS.items():
            vec = self._embed(text, "search_document")
            if vec is not None:
                self._domain_vecs[cat] = vec

        for cat, text in WEB_ANCHORS.items():
            vec = self._embed(text, "search_document")
            if vec is not None:
                self._web_vecs[cat] = vec

        elapsed = (time.perf_counter() - t) * 1000
        logger.info(f"✅ {len(self._domain_vecs)} domain + {len(self._web_vecs)} web anchors "
                    f"precomputed in {elapsed:.0f}ms")

    def _load_personal_anchors(self):
        """
        Builds personal node anchors from live bucket JSON files.
        Called at routing time if vault has been updated since last scan.
        Non-blocking — uses cached vectors between scans.
        """
        if not self.vault_path or not os.path.exists(self.vault_path):
            return

        now = time.time()
        if now - self._last_vault_scan < self._vault_scan_interval:
            return  # Use cached anchors

        self._last_vault_scan = now
        new_vecs = {}

        for json_file in glob.glob(os.path.join(self.vault_path, "**", "Bucket_*.json"),
                                   recursive=True):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                bucket = data.get("bucket", "Unknown")
                for node_name, attrs in data.get("nodes", {}).items():
                    # Build rich text from node name + all attributes
                    if isinstance(attrs, dict):
                        attr_text = " ".join(f"{k} {v}" for k, v in attrs.items())
                    else:
                        attr_text = str(attrs)
                    anchor_text = f"{bucket} {node_name} {attr_text}"
                    anchor_key  = f"{bucket}::{node_name}"

                    vec = self._embed(anchor_text, "search_document")
                    if vec is not None:
                        new_vecs[anchor_key] = vec
            except Exception as e:
                logger.warning(f"⚠️ Failed to load personal anchor from {json_file}: {e}")

        with self._personal_lock:
            self._personal_vecs = new_vecs

        if new_vecs:
            logger.info(f"🧠 Personal anchors updated: {len(new_vecs)} nodes loaded")

    def register_node(self, bucket: str, node_name: str, attrs: dict):
        """
        Called by RealtimeMemoryCapture._save_fact() when a new node is written.
        Updates the personal anchor map immediately without waiting for the scan interval.
        """
        if isinstance(attrs, dict):
            attr_text = " ".join(f"{k} {v}" for k, v in attrs.items())
        else:
            attr_text = str(attrs)
        anchor_text = f"{bucket} {node_name} {attr_text}"
        anchor_key  = f"{bucket}::{node_name}"

        vec = self._embed(anchor_text, "search_document")
        if vec is not None:
            with self._personal_lock:
                self._personal_vecs[anchor_key] = vec
            logger.debug(f"🧠 Personal anchor registered: {anchor_key}")

    # ----------------------------------------------------------
    # EMBEDDING
    # ----------------------------------------------------------
    def _embed(self, text: str, prefix: str = "search_query") -> Optional[np.ndarray]:
        try:
            payload = {
                "model": NOMIC_MODEL,
                "prompt": f"{prefix}: {text}",
                "keep_alive": -1
            }
            req = urllib.request.Request(
                OLLAMA_EMBED_URL,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=15.0) as res:
                return np.array(json.loads(res.read().decode('utf-8'))['embedding'])
        except Exception as e:
            logger.error(f"⚠️ Nomic embed failed: {e}")
            return None

    @staticmethod
    def _best_score(q_vec: np.ndarray, anchor_vecs: dict) -> tuple:
        if not anchor_vecs:
            return 0.0, None
        scores = {}
        for k, v in anchor_vecs.items():
            denom = np.linalg.norm(q_vec) * np.linalg.norm(v)
            scores[k] = float(np.dot(q_vec, v) / denom) if denom > 0 else 0.0
        best_k = max(scores, key=scores.get)
        return scores[best_k], best_k

    # ----------------------------------------------------------
    # TOPIC CLASSIFICATION (for filler selection)
    # Nomic only — must stay fast, called on every query
    # ----------------------------------------------------------
    def get_topic_with_score(self, text: str) -> tuple:
        """
        Returns (topic, confidence) for filler audio selection.
        Pure Nomic — no LLM call. Always fast.
        Called by engine.py to decide play_filler and bridge behaviour.
        """
        if not self._domain_vecs:
            return "general", 0.0
        try:
            q_vec = self._embed(text, "search_query")
            if q_vec is None:
                return "general", 0.0
            score, topic = self._best_score(q_vec, self._domain_vecs)
            return topic or "general", float(score)
        except Exception as e:
            logger.error(f"⚠️ Topic scoring failed: {e}")
            return "general", 0.0

    # ----------------------------------------------------------
    # ROUTING DECISION
    # Two-stage: Nomic fast-path → mistral gray zone
    # ----------------------------------------------------------
    def _nomic_routing_decision(self, q_vec: np.ndarray) -> tuple:
        """
        Stage 1: Pure Nomic cosine scoring.
        Returns (is_web, is_personal, web_score, web_key, personal_score, personal_key, is_gray)
        is_gray=True means at least one dimension is ambiguous — needs LLM confirmation.
        """
        with self._personal_lock:
            personal_vecs = dict(self._personal_vecs)

        web_score,      web_key      = self._best_score(q_vec, self._web_vecs)
        personal_score, personal_key = self._best_score(q_vec, personal_vecs)

        web_clear_yes = web_score      >= CLEAR_HIGH
        web_clear_no  = web_score      <= CLEAR_LOW
        per_clear_yes = personal_score >= CLEAR_HIGH
        per_clear_no  = personal_score <= CLEAR_LOW

        web_gray = not web_clear_yes and not web_clear_no
        per_gray = not per_clear_yes and not per_clear_no
        is_gray  = web_gray or per_gray

        return (
            web_clear_yes, per_clear_yes,
            web_score, web_key,
            personal_score, personal_key,
            is_gray
        )

    def _mistral_routing_decision(self, query: str) -> str:
        """
        Stage 2: LLM gray zone classifier.
        Only called when Nomic is ambiguous.
        Model is already hot in RAM — actual latency ~200-300ms.
        Returns one of: web / memory / both / general
        """
        prompt = ROUTING_PROMPT.format(query=query)
        payload = {
            "model": ROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 4}
        }
        try:
            res = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=20.0)
            raw = res.json().get("message", {}).get("content", "").strip().lower()
            for cat in ["both", "memory", "web", "general"]:
                if cat in raw:
                    return cat
            return "general"
        except Exception as e:
            logger.error(f"⚠️ {ROUTER_MODEL} routing failed: {e}")
            return "general"

    def _get_route(self, query: str, q_vec: np.ndarray) -> dict:
        """
        Master routing decision.
        Returns dict with: decision, web_score, personal_score,
        matched_node, used_llm, route_latency_ms
        """
        t = time.perf_counter()

        (is_web, is_personal,
         web_score, web_key,
         personal_score, personal_key,
         is_gray) = self._nomic_routing_decision(q_vec)

        used_llm = False

        if is_gray:
            # Gray zone — mistral decides
            decision = self._mistral_routing_decision(query)
            used_llm = True
            logger.info(f"🤔 Gray zone → mistral: '{decision}'")
        else:
            if is_web and is_personal:
                decision = "both"
            elif is_web:
                decision = "web"
            elif is_personal:
                decision = "memory"
            else:
                decision = "general"

        return {
            "decision":       decision,
            "web_score":      round(web_score, 3),
            "web_key":        web_key,
            "personal_score": round(personal_score, 3),
            "matched_node":   personal_key,
            "used_llm":       used_llm,
            "route_ms":       round((time.perf_counter() - t) * 1000, 1),
        }

    def _is_local_session_query(self, query: str) -> bool:
        q = re.sub(r"\s+", " ", query.lower()).strip()
        return (
            any(phrase in q for phrase in [
                "how many queries have i made",
                "how many questions have i asked",
                "how many queries did i make",
                "how many prompts have i sent",
            ])
            and "so far" in q
        )

    def _extract_weather_location(self, query: str) -> Optional[str]:
        q = re.sub(r"\s+", " ", query.lower()).strip()
        match = re.search(
            r"\b(?:weather|wheather|temperature|forecast)\b.*?\bin\s+([a-zA-Z][a-zA-Z\s.\-]{1,60}?)(?:\s+\b(?:now|today|currently)\b)?[?.! ]*$",
            q,
        )
        if match:
            return match.group(1).strip(" ?.!").title()
        return None

    def _force_live_web_query(self, query: str) -> bool:
        q = re.sub(r"\s+", " ", query.lower()).strip()
        freshness_words = ["latest", "newest", "current", "recent", "today"]
        product_words = ["phone", "smartphone", "mobile", "model", "version", "release", "launched", "launch", "flagship", "series"]
        if self._extract_weather_location(q):
            return True
        if any(token in q for token in freshness_words) and any(token in q for token in product_words):
            return True
        if any(token in q for token in ["iphone", "samsung", "pixel", "xiaomi", "oneplus", "oppo", "vivo", "nothing"]) and any(
            token in q for token in freshness_words + ["phone", "smartphone", "mobile", "flagship", "model"]
        ):
            return True
        if any(token in q for token in ["btc", "bitcoin", "ethereum", "eth", "crypto", "cryptocurrency"]) and any(word in q for word in ["price", "live", "current", "usd", "value"]):
            return True
        if any(token in q for token in ["stock", "share", "nse", "bse", "nasdaq", "nyse", "sensex", "nifty"]) and any(
            word in q for word in ["price", "live", "current", "quote", "value"]
        ):
            return True
        if any(token in q for token in ["technology", "tech", "software", "hardware", "ai", "artificial intelligence", "startup", "open source"]) and any(
            word in q for word in ["news", "latest", "today", "update", "updates", "headline", "headlines", "facts", "what is", "explain", "tell me about"]
        ):
            return True
        if "review" in q and any(token in q for token in ["movie", "film", "bollywood", "hollywood", "durandhar"]):
            return True
        if any(token in q for token in ["bollywood", "hollywood", "box office", "movie", "film"]) and any(
            word in q for word in ["news", "today", "latest", "release", "box office"]
        ):
            return True
        if any(token in q for token in ["schedule", "fixture", "fixtures"]) and "ipl" in q:
            return True
        if "news" in q and any(word in q for word in ["today", "latest", "top", "headline", "headlines", "breaking"]):
            return True
        if any(token in q for token in ["diwali", "deepavali"]) and any(word in q for word in ["when is", "date", "this year", "year"]):
            return True
        if (
            "president" in q
            and any(word in q for word in ["america", "united states", "usa"])
            and any(word in q for word in ["who is", "current", "today", "now", "right now"])
        ):
            return True
        if (
            "prime minister" in q
            and "india" in q
            and any(word in q for word in ["who is", "current", "today", "now", "right now"])
        ):
            return True
        return False

    def _rule_based_web_query(self, query: str) -> Optional[str]:
        q = re.sub(r"\s+", " ", query.lower()).strip()
        freshness_words = ["latest", "newest", "current", "recent", "today"]
        product_words = ["phone", "smartphone", "mobile", "model", "version", "release", "launched", "launch", "flagship", "series"]
        location = self._extract_weather_location(q)
        if location:
            return f"weather {location}"
        if any(token in q for token in freshness_words) and any(token in q for token in product_words):
            return q
        if any(token in q for token in ["iphone", "samsung", "pixel", "xiaomi", "oneplus", "oppo", "vivo", "nothing"]) and any(
            token in q for token in freshness_words + ["phone", "smartphone", "mobile", "flagship", "model"]
        ):
            return q
        if any(token in q for token in ["btc", "bitcoin", "ethereum", "eth", "crypto", "cryptocurrency"]) and any(
            word in q for word in ["price", "live", "current", "usd", "value"]
        ):
            return q
        if any(token in q for token in ["stock", "share", "nse", "bse", "nasdaq", "nyse", "sensex", "nifty"]) and any(
            word in q for word in ["price", "live", "current", "quote", "value"]
        ):
            return q
        if any(token in q for token in ["technology", "tech", "software", "hardware", "ai", "artificial intelligence", "startup", "open source"]) and any(
            word in q for word in ["news", "latest", "today", "update", "updates", "headline", "headlines", "facts", "what is", "explain", "tell me about"]
        ):
            return q
        if "review" in q and any(token in q for token in ["movie", "film", "bollywood", "hollywood", "durandhar"]):
            return q
        if any(token in q for token in ["bollywood", "hollywood", "box office", "movie", "film"]) and any(
            word in q for word in ["news", "today", "latest", "release", "box office"]
        ):
            return q
        if any(token in q for token in ["schedule", "fixture", "fixtures"]) and "ipl" in q:
            return q
        if "news" in q and any(word in q for word in ["today", "latest", "top", "headline", "headlines", "breaking"]) and not any(
            token in q for token in ["bollywood", "hollywood", "movie", "film", "box office"]
        ):
            return "top news today"
        if any(token in q for token in ["diwali", "deepavali"]) and any(word in q for word in ["when is", "date", "this year", "year"]):
            year_match = re.search(r"\b(20\d{2})\b", q)
            target_year = year_match.group(1) if year_match else str(datetime.now().year)
            return f"diwali {target_year} date"
        if (
            "president" in q
            and any(word in q for word in ["america", "united states", "usa"])
            and any(word in q for word in ["who is", "current", "today", "now", "right now"])
        ):
            return "current president of the united states"
        if (
            "prime minister" in q
            and "india" in q
            and any(word in q for word in ["who is", "current", "today", "now", "right now"])
        ):
            return "current prime minister of india"
        return None

    # ----------------------------------------------------------
    # WEB TOOLS
    # ----------------------------------------------------------
    def _optimize_web_query(self, user_query: str,
                             personal_context: str = "") -> str:
        """
        Converts conversational query to a search engine string.
        personal_context — injected when route=both so the optimizer
        knows the actual subject (e.g. "iPhone 12") before rewriting.
        This prevents "upgrade it" → "best options for upgrading it"
        and instead produces "best iPhone 12 upgrade 2026".
        """
        forced_query = self._rule_based_web_query(user_query)
        if forced_query:
            logger.info(f"🔍 Query optimised (rule): '{user_query}' → '{forced_query}'")
            return forced_query

        context_block = (
            f"USER'S PERSONAL CONTEXT: {personal_context}\n\n"
            if personal_context else ""
        )
        prompt = (
            f"Today is {time.strftime('%B %d, %Y')}. "
            f"Convert this request into a short, effective search engine query.\n"
            f"Rules:\n"
            f"1. Keep specific model names and entities.\n"
            f"2. Remove filler words.\n"
            f"3. Use {time.strftime('%Y')} as the current year.\n"
            f"4. IMPORTANT: If the request says 'my phone', 'my laptop', etc., look at the USER'S PERSONAL CONTEXT and replace it with the actual model name.\n"
            f"5. DO NOT add words like 'news', 'update', or 'latest' unless the user expressly asked for them.\n"
            f"6. Max 6 words.\n"
            f"Output ONLY the raw search string. No quotes, no explanation.\n\n"
            f"{context_block}"
            f"Request: '{user_query}'"
        )
        payload = {
            "model":   ROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream":  False,
            "keep_alive": -1,
            "options": {"temperature": 0.0, "num_predict": 15}
        }
        try:
            res      = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=8.0)
            optimized = (res.json().get("message", {}).get("content", "")
                         .replace('"', '').replace("'", '').strip())
            # Reject if empty or suspiciously long
            if not optimized or len(optimized.split()) > 10:
                return user_query
            original_tokens = {
                token for token in re.findall(r"[a-z0-9]+", user_query.lower())
                if token not in {"the", "a", "an", "is", "are", "what", "who", "how", "me", "for", "now", "today", "live"}
            }
            optimized_tokens = set(re.findall(r"[a-z0-9]+", optimized.lower()))
            if original_tokens and not (original_tokens & optimized_tokens):
                logger.warning(f"⚠️ Query optimisation rejected unrelated rewrite: '{optimized}'")
                return user_query
            logger.info(f"🔍 Query optimised: '{user_query}' → '{optimized}'")
            return optimized
        except Exception as e:
            logger.error(f"⚠️ Query optimisation failed: {e}")
            return user_query

    def _quick_web_lookup(self, query: str) -> Optional[str]:
        """
        Fetches SearxNG results and synthesises them with mistral.
        Returns formatted string or None on failure.
        Never returns "SEARCH_FAILED_OR_EMPTY" — that string is set
        by the caller only as a last resort after all fallbacks fail.
        """
        # ── SearxNG fetch ─────────────────────────────────────
        search_queries = self._expand_search_queries(query)
        results = []
        primary_query = search_queries[0]
        results = self._filter_search_results(self._fetch_searxng(primary_query), primary_query)
        
        if results is None:
            # Fatal connection error — do not retry, jump straight to DDG
            results = []
        else:
            # Fallback 1: drop time_range and retry
            if not results:
                logger.info(f"🌐 Retrying without time filter: {primary_query}")
                results = self._filter_search_results(self._fetch_searxng(primary_query, time_range=None), primary_query)
                if results is None: results = []

            # Fallback 2: try normalized search variants, preserving the core terms
            if not results:
                for variant in search_queries[1:]:
                    logger.info(f"🌐 Retrying simplified: '{variant}'")
                    results = self._filter_search_results(self._fetch_searxng(variant, time_range=None), variant)
                    if results is None: results = []
                    if results:
                        break

        if not results:
            logger.warning(f"🌐 All SearxNG attempts failed for: '{query}'")
            for variant in search_queries:
                results = self._filter_search_results(self._fetch_duckduckgo(variant), variant)
                if results:
                    break

        if not results:
            logger.warning(f"🌐 All web search backends exhausted for: '{query}'")
            # Queue async deep research via OpenClaw worker
            try:
                from core.database_manager import DatabaseManager
                db = DatabaseManager()
                db.insert_openclaw_job(
                    "research", query,
                    {"topic": query, "depth": "detailed"},
                    priority=3
                )
            except Exception:
                pass
            return None

        # ── Synthesise with mistral ────────────────────────────
        raw_context = "\n".join(
            f"SOURCE: {r.get('title', 'Unknown')}\n"
            f"SNIPPET: {r.get('content', r.get('snippet', ''))}"
            for r in results[:8]
        )

        summary_prompt = (
            f"You are a research assistant. Today is {time.strftime('%B %d, %Y')}.\n"
            f"Summarise these search results into 2 very concise factual bullet points.\n"
            f"Focus on the most important, distinct facts. No intro or outro.\n"
            f"Each bullet: max 15 words.\n\n"
            f"SEARCH RESULTS:\n{raw_context}"
        )

        payload = {
            "model":   ROUTER_MODEL,
            "messages": [{"role": "user", "content": summary_prompt}],
            "stream":  False,
            "keep_alive": -1,
            "options": {"temperature": 0.1, "num_predict": 150}
        }
        try:
            res     = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=20.0)
            summary = res.json().get("message", {}).get("content", "").strip()
            if not summary:
                return None
            logger.info(f"🌐 Web synthesis complete ({len(results)} results)")
            return f"--- LIVE WEB ({time.strftime('%B %d, %Y')}) ---\n{summary}"
        except Exception as e:
            logger.error(f"🌐 Synthesis failed: {e}")
            # Return raw snippets as fallback so LLM gets something
            fallback = "\n".join(
                f"- {r.get('title','')}: {r.get('content','')[:100]}"
                for r in results[:5]
            )
            return f"--- WEB SNIPPETS ---\n{fallback}" if fallback else None

    def _fetch_searxng(self, query: str,
                        time_range: Optional[str] = "day") -> list:
        """
        Raw SearxNG fetch. Returns list of result dicts or empty list.
        Separated from synthesis so retries are clean.
        """
        if time.time() < self._searxng_dead_until:
            logger.info("🌐 SearxNG skipped during offline cooldown window")
            return None
        try:
            params = {
                "q":      query,
                "format": "json",
                "engines": "google,bing,duckduckgo",
                "language": "en-US",
            }
            if time_range:
                params["time_range"] = time_range

            last_error = None
            for searxng_url in self._searxng_urls:
                logger.info(f"🌐 SearxNG: '{query}' via {searxng_url}" +
                            (f" [{time_range}]" if time_range else " [all time]"))
                try:
                    res = requests.get(
                        searxng_url,
                        params=params,
                        timeout=5.0,
                        headers={
                            "User-Agent": "Mozilla/5.0",
                            "X-Forwarded-For": "127.0.0.1",
                            "X-Real-IP": "127.0.0.1",
                        },
                    )
                    res.raise_for_status()
                    data = res.json()
                    return data.get("results", [])
                except requests.exceptions.ConnectionError as ce:
                    last_error = ce
                    continue
            if last_error is not None:
                self._searxng_dead_until = time.time() + SEARXNG_OFFLINE_COOLDOWN_SEC
                logger.warning(f"🌐 SearxNG is totally offline (ConnectionError): {last_error}")
                return None
            return []
        except requests.exceptions.ConnectionError as ce:
            self._searxng_dead_until = time.time() + SEARXNG_OFFLINE_COOLDOWN_SEC
            logger.warning(f"🌐 SearxNG is totally offline (ConnectionError): {ce}")
            return None  # None indicates fatal network failure, skip retries
        except Exception as e:
            logger.warning(f"🌐 SearxNG request failed: {e}")
            return []

    def _expand_search_queries(self, query: str) -> list[str]:
        base = re.sub(r"\s+", " ", query.strip())
        variants = []
        if base:
            variants.append(base)

        lowered = base.lower()
        simplified = re.sub(
            r"^(what|which|who|when|where|why|how)\s+(is|are|was|were|does|do)\s+(the\s+)?",
            "",
            lowered,
        )
        simplified = re.sub(r"^(tell me|show me|fetch me|find me)\s+", "", simplified)
        simplified = re.sub(r"\s+", " ", simplified).strip(" ?.!")
        if simplified and simplified not in variants:
            variants.append(simplified)
        return variants

    def _filter_search_results(self, results: Optional[list], query: str) -> Optional[list]:
        if results is None:
            return None
        if not results:
            return []

        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "what", "which", "who", "when",
            "where", "why", "how", "me", "for", "latest", "current", "recent", "today",
            "model", "version", "release",
        }
        query_tokens = [
            token for token in re.findall(r"[a-z0-9]+", query.lower())
            if token not in stopwords and len(token) > 2
        ]
        if not query_tokens:
            return results

        filtered = []
        for result in results:
            haystack = " ".join([
                str(result.get("title", "")),
                str(result.get("content", "")),
                str(result.get("url", "")),
            ]).lower()
            overlap = sum(1 for token in query_tokens if token in haystack)
            if overlap >= 1:
                filtered.append(result)

        return filtered or []

    def _load_ddgs_class(self):
        try:
            from ddgs import DDGS
            return DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
                return DDGS
            except ImportError:
                return None

    def _fetch_duckduckgo(self, query: str) -> list:
        """
        Fallback web search using DDGS.
        """
        DDGS = self._load_ddgs_class()
        if DDGS is None:
            logger.error("🦆 DDGS missing! Install `ddgs` (preferred) or `duckduckgo-search`.")
            return []
        try:
            logger.info(f"🦆 DuckDuckGo (DDGS) fallback: '{query}'")
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(
                    query,
                    region="us-en",
                    safesearch="moderate",
                    backend="google,duckduckgo,brave,yahoo",
                    max_results=5,
                ):
                    results.append({
                        "title": r.get("title", ""),
                        "content": r.get("body", ""),
                        "url": r.get("href", "")
                    })
            if results:
                logger.info(f"🦆 DDGS returned {len(results)} results")
            else:
                logger.info(f"🦆 DDGS: no results found for '{query}'")
            return results
        except Exception as e:
            logger.warning(f"🦆 DuckDuckGo (DDGS) fallback failed: {e}")
            return []

    # ----------------------------------------------------------
    # PRONOUN / VAGUENESS RESOLUTION
    # Runs in parallel with routing — never blocks
    # ----------------------------------------------------------
    def _resolve_context(self, user_input: str, history: str) -> str:
        """
        Resolves vague pronouns using conversation history.
        Only fires when pronouns or very short inputs are detected.
        Returns original input unchanged if resolution fails or hallucinates.
        """
        needs_resolve = (
            any(w in user_input.lower().split()
                for w in ["this", "that", "it", "they", "he", "she"])
            or len(user_input.split()) < 4
        )

        if not needs_resolve or not history.strip():
            return user_input

        prompt = (
            f"Look at the history and resolve vague references in the user input "
            f"into a standalone sentence. If input already makes sense on its own, "
            f"return it exactly unchanged.\n"
            f"Output ONLY the final text. No explanation.\n\n"
            f"History: {history[-300:]}\n"
            f"Input: '{user_input}'\n"
            f"Output:"
        )
        payload = {
            "model": ROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 40}
        }
        try:
            res = requests.post(OLLAMA_CHAT_URL, json=payload, timeout=8.0)
            resolved = res.json().get("message", {}).get("content", "").strip().replace('"', '')

            # Reject hallucinations:
            # 1. Parenthetical reasoning artifacts — "(This input does not...)"
            if '(' in resolved and ')' in resolved:
                logger.warning(f"⚠️ Resolver rejected (parenthetical): '{resolved[:60]}'")
                return user_input
            # 2. Disproportionate length increase
            input_len = len(user_input.split())
            resolved_len = len(resolved.split())
            if resolved_len > input_len + 5 or resolved_len > input_len * 2:
                logger.warning(f"⚠️ Resolver rejected (too long): {resolved_len} vs {input_len} words")
                return user_input
            # 3. Empty or garbage
            if not resolved or not any(c.isalpha() for c in resolved):
                return user_input

            if resolved.lower().strip() != user_input.lower().strip():
                logger.info(f"🔄 Resolved: '{user_input}' → '{resolved}'")
            return resolved
        except Exception:
            return user_input

    # ----------------------------------------------------------
    # ENRICH PACKET — master switchboard
    # Nomic topic + parallel routing decision
    # ----------------------------------------------------------
    def enrich_packet(self, packet: dict) -> dict:
        """
        Main entry point from engine._handle_normal_command().
        Enriches GoldenPacket with:
          - route_taken (web / memory / both / general_no_web)
          - web_data (if web route)
          - topic + confidence (for filler selection)
          - matched_node (personal node for memory injection)
        """
        user_input = packet.get('input', '')
        history    = packet.get('history', '')
        if not user_input:
            return packet

        t_total = time.perf_counter()

        # Refresh personal anchors from vault if needed
        self._load_personal_anchors()

        # ── Stage 1: Single Nomic embedding (shared by all downstream scoring) ──
        t_embed = time.perf_counter()
        q_vec = self._embed(user_input, "search_query")
        embed_ms = (time.perf_counter() - t_embed) * 1000

        if q_vec is None:
            packet['route_taken'] = "general_no_web"
            return packet

        # ── Stage 2: Parallel execution ──────────────────────────────────────
        # A. Topic classification (Nomic only, instant) — for filler selection
        # B. Routing decision (Nomic fast-path, mistral gray zone)
        # C. Context resolution (mistral, only if pronouns detected)
        # All three use the same q_vec from Stage 1

        topic_result   = {}
        routing_result = {}
        resolved_input = user_input

        def _run_topic():
            score, best = self._best_score(q_vec, self._domain_vecs)
            topic_result['topic'] = best or "general"
            topic_result['score'] = score

        def _run_routing():
            routing_result.update(self._get_route(user_input, q_vec))

        def _run_resolve():
            nonlocal resolved_input
            resolved_input = self._resolve_context(user_input, history)

        threads = [
            threading.Thread(target=_run_topic,   daemon=True),
            threading.Thread(target=_run_routing, daemon=True),
            threading.Thread(target=_run_resolve, daemon=True),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=12.0)  # Hard cap — never block engine indefinitely

        # ── Apply topic result ────────────────────────────────────────────────
        topic      = topic_result.get('topic', 'general')
        confidence = topic_result.get('score', 0.0)

        logger.info(f"🛰️  Topic: {topic} | Confidence: {confidence:.4f}")

        # ── Apply resolved input ──────────────────────────────────────────────
        if resolved_input != user_input:
            packet['input'] = resolved_input

        # ── Apply routing result ──────────────────────────────────────────────
        decision     = routing_result.get('decision', 'general')
        matched_node = routing_result.get('matched_node')
        used_llm     = routing_result.get('used_llm', False)
        route_ms     = routing_result.get('route_ms', 0)

        if self._is_local_session_query(resolved_input):
            decision = "general"
            logger.info("🧭 Heuristic override → general (local session query)")
        elif self._force_live_web_query(resolved_input):
            decision = "web"
            logger.info("🧭 Heuristic override → web (current/live query)")

        logger.info(
            f"🗺️  Route: {decision} | "
            f"web={routing_result.get('web_score',0):.2f} "
            f"mem={routing_result.get('personal_score',0):.2f} "
            f"{'[LLM]' if used_llm else '[Nomic]'} | {route_ms:.0f}ms"
        )

        # Store matched node for ContextAssembler to fetch the actual data
        if matched_node:
            packet['matched_memory_node'] = matched_node

        # ── Execute route ─────────────────────────────────────────────────────
        if decision == "general":
            packet['route_taken'] = "general_no_web"

        elif decision == "memory":
            packet['route_taken'] = "general_no_web"
            packet['needs_memory'] = True

        elif decision in ("web", "both"):
            if decision == "both":
                packet['needs_memory'] = True

            # 🟢 Pass full memory context to optimizer so it can resolve things like "my phone"
            mem_ctx_str = packet.get("memory_context", "")
            optimized = self._optimize_web_query(resolved_input, mem_ctx_str)
            
            # 🟢 APIScout first — check if we have a dedicated API for this query
            web_data = None
            if self.api_scout:
                web_data = self.api_scout.lookup_api(resolved_input, q_vec)
                if web_data:
                    logger.info(f"🌐 Intercepted Web Search with APIScout successfully.")
            
            # 🟢 No API match — fall back to SearxNG / DuckDuckGo
            if not web_data:
                web_data = self._quick_web_lookup(optimized)

            if web_data:
                packet['route_taken'] = "general_web_search"
                packet['web_data']    = web_data
            else:
                # 🟢 Web failed — preserve live-query failure so the engine can fail closed.
                logger.warning(
                    f"🌐 Web search failed for '{optimized}' — "
                    f"preserving live-query failure state"
                )
                packet['route_taken'] = "general_web_unavailable"
                packet['web_data']    = None
                packet['live_lookup_failed'] = True
                packet['failed_web_query'] = optimized
                # Inject a note so LLM knows web was attempted but failed
                packet['memory_tank'] = (
                    f"[SYSTEM INSTRUCTION: Live web search is currently offline. "
                    f"You MUST explicitly inform the user that you cannot fetch live/current data. "
                    f"DO NOT guess, invent, or use the conversation history to fabricate news/data.]"
                )

        total_ms = (time.perf_counter() - t_total) * 1000
        logger.info(
            f"⚡ enrich_packet done | "
            f"embed={embed_ms:.0f}ms total={total_ms:.0f}ms"
        )

        return packet
