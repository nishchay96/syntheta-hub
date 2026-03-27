import os
import json
import logging
import urllib.request
import numpy as np
import requests
import re
import time
from datetime import datetime, timedelta
import threading
import concurrent.futures
from collections import OrderedDict
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import pandas as pd
from core.database_manager import DatabaseManager

logger = logging.getLogger("APIScout")

OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embeddings"
NOMIC_MODEL = "nomic-embed-text:v1.5"

OFFICEHOLDER_QUERIES = {
    ("president", "united states"): {
        "label": "President of the United States",
        "source": "Wikidata",
        "query": """
        SELECT ?personLabel ?start WHERE {
          ?person p:P39 ?st.
          ?st ps:P39 wd:Q11696; pq:P580 ?start.
          FILTER NOT EXISTS { ?st pq:P582 ?end }
          ?person wdt:P31 wd:Q5.
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        ORDER BY DESC(?start)
        LIMIT 1
        """,
    },
    ("prime minister", "india"): {
        "label": "Prime Minister of India",
        "source": "Wikidata",
        "query": """
        SELECT ?personLabel WHERE {
          wd:Q668 wdt:P6 ?person .
          SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
        }
        LIMIT 1
        """,
    },
}

SPORTS_SCHEDULE_SOURCES = {
    "ipl": {
        "label": "Indian Premier League",
        "source": "Cricbuzz",
        "urls": lambda year: [
            f"https://www.cricbuzz.com/cricket-series/9241/indian-premier-league-{year}/matches",
            f"https://www.cricbuzz.com/cricket-series/indian-premier-league-{year}/matches",
        ],
    },
}

class APIScout:
    def __init__(self):
        self.catalog_path = os.path.join(os.path.dirname(__file__), "api_catalog.json")
        self.db = DatabaseManager()
        self.current_user = None
        self.categories = {}
        self.category_vectors = {}
        needs_fetch = self._load_or_fetch_catalog()
        
        self.maintenance_thread = threading.Thread(target=self._maintenance_loop, args=(needs_fetch,), daemon=True)
        self.maintenance_thread.start()

    def _maintenance_loop(self, initial_fetch=False):
        """Periodically refreshes and verifies the API catalog."""
        if initial_fetch:
            logger.info("🔄 APIScout: Running background catalog fetch...")
            self._fetch_github_apis()
            self._load_or_fetch_catalog(force=False)
            
        while True:
            # Refresh every 24 hours
            time.sleep(24 * 3600)
            logger.info("🔄 APIScout: Starting periodic catalog maintenance...")
            self._fetch_github_apis()
            self._load_or_fetch_catalog(force=False)

    def _embed(self, text: str, task_type: str = "search_document") -> np.ndarray:
        try:
            # Nomic v1.5 requires specific prefixes for search
            prefix = f"{task_type}: "
            payload = {
                "model": NOMIC_MODEL,
                "prompt": f"{prefix}{text}",
                "keep_alive": -1
            }
            req = urllib.request.Request(
                OLLAMA_EMBED_URL,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=15.0) as res:
                raw = res.read().decode('utf-8')
                return np.array(json.loads(raw)['embedding'])
        except Exception as e:
            logger.error(f"⚠️ APIScout Nomic embed failed: {e}")
            return None

    def _load_or_fetch_catalog(self, force=False):
        # Auto-refresh if older than 48 hours
        needs_fetch = force
        if not force and os.path.exists(self.catalog_path):
            mtime = os.path.getmtime(self.catalog_path)
            if time.time() - mtime > 48 * 3600:
                needs_fetch = True
        elif not os.path.exists(self.catalog_path):
            needs_fetch = True
                
        try:
            if os.path.exists(self.catalog_path):
                with open(self.catalog_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.categories = data.get("categories", {})
                
                # Precompute category vectors for mapping (as documents)
                for cat in self.categories.keys():
                    self.category_vectors[cat] = self._embed(cat, task_type="search_document")
                    
                total_apis = sum(len(apis) for apis in self.categories.values())
                logger.info(f"🌐 APIScout Loaded {len(self.categories)} categories with {total_apis} NO-AUTH APIs.")
        except Exception as e:
            logger.error(f"❌ Failed to load API Catalog: {e}")
            
        return needs_fetch

    def _fetch_github_apis(self):
        logger.info("🌐 APIScout: Fetching fresh API catalog from GitHub Public APIs...")
        url = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15.0) as res:
                text = res.read().decode('utf-8')
        except Exception as e:
            logger.error(f"❌ GitHub Fetch Failed: {e}")
            return

        parsed_cats = {}
        current_category = None
        
        for line in text.split('\n'):
            cat_match = re.match(r'^###\s+(.*)', line)
            if cat_match:
                current_category = cat_match.group(1).strip()
                parsed_cats[current_category] = []
                continue
                
            if current_category and (line.startswith('| [') or line.startswith('|[')):
                cols = [col.strip() for col in line.split('|')[1:-1]]
                if len(cols) >= 3:
                    name_link = re.match(r'\[(.*?)\]\((.*?)\)', cols[0])
                    auth_col = cols[2].lower()
                    # REQUIRE No-Auth or Empty Auth
                    if name_link and auth_col in ["no", "", "none"]:
                        name, link = name_link.groups()
                        parsed_cats[current_category].append({
                            "name": name,
                            "url": link,
                            "description": cols[1]
                        })
                        
        parsed_cats = {k: v for k, v in parsed_cats.items() if len(v) > 0}
        
        # Verify APIs, keeping up to 3 working ones per category
        verified_cats = {}
        
        def _check_api(api):
            try:
                res = requests.get(api["url"], timeout=3.0, headers={"User-Agent": "Mozilla/5.0"})
                if res.status_code == 200:
                    return api
            except Exception:
                pass
            return None

        for cat, apis in parsed_cats.items():
            working = []
            for i in range(0, len(apis), 5):
                chunk = apis[i:i+5]
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    results = list(executor.map(_check_api, chunk))
                for res in results:
                    if res:
                        working.append(res)
                        if len(working) >= 3:
                            break
                if len(working) >= 3:
                    break
            
            if working:
                verified_cats[cat] = working
        
        with open(self.catalog_path, 'w', encoding='utf-8') as f:
            json.dump({
                "last_updated": datetime.now().isoformat(),
                "categories": verified_cats
            }, f, indent=2)
            
        logger.info("✅ APIScout Catalog updated with verified endpoints.")

    def match_bucket_to_category(self, bucket_name: str, query_vector=None) -> str:
        """Matches a user bucket or query to a GitHub category."""
        if not self.categories:
            return None
            
        b_low = self._normalize_query(bucket_name)
        cat_names = list(self.categories.keys())

        priority_aliases = [
            (["bollywood", "hollywood", "box office", "movie", "movies", "film", "films", "release", "releases", "review"], "Entertainment"),
            (["technology", "tech", "software", "hardware", "ai", "artificial intelligence", "startup", "open source", "docker", "kubernetes", "linux", "github", "programming"], "Development"),
            (["stock", "stocks", "share", "shares", "nse", "bse", "nasdaq", "nyse", "sensex", "nifty"], "Finance"),
            (["crypto", "cryptocurrency", "bitcoin", "btc", "ethereum", "eth", "solana", "dogecoin", "xrp"], "Cryptocurrency"),
            (["iphone", "samsung", "pixel", "xiaomi", "oneplus", "oppo", "vivo", "nothing", "phone", "smartphone", "mobile"], "Phone"),
            (["weather", "forecast", "temperature"], "Weather"),
            (["prime minister", "president", "government", "diwali", "deepavali", "holiday"], "Government"),
            (["tweet", "tweets", "post", "posts", "truth social"], "Social"),
            (["live score", "score", "match", "cricket", "football", "nba", "nfl", "ipl"], "Sports & Fitness"),
            (["news", "trending", "headline", "headlines"], "News"),
        ]
        for needles, target_cat in priority_aliases:
            if target_cat in self.categories and any(needle in b_low for needle in needles):
                return target_cat
        
        # 1. Exact or Substring Match (Case-Insensitive)
        for cat in cat_names:
            cat_l = cat.lower()
            if cat_l == b_low or cat_l in b_low or b_low in cat_l:
                return cat

        # 2. Hardcoded Semantic Aliases (Checking each word in the query)
        aliases = {
            "work": "Business", "career": "Jobs", "finance": "Finance", "money": "Finance",
            "investing": "Blockchain", "crypto": "Cryptocurrency", "bitcoin": "Cryptocurrency",
            "btc": "Cryptocurrency",
            "ethereum": "Cryptocurrency", "stocks": "Finance", "stock": "Finance", "share": "Finance",
            "shares": "Finance", "market": "Finance", "nse": "Finance", "bse": "Finance",
            "nasdaq": "Finance", "nyse": "Finance", "sensex": "Finance", "nifty": "Finance",
            "cooking": "Food & Drink", "diet": "Food & Drink", "dining": "Food & Drink",
            "travel": "Transportation", "cars": "Vehicle", "fitness": "Sports & Fitness", "gym": "Sports & Fitness",
            "coding": "Programming", "dev": "Development", "tech": "Development", "technology": "Development",
            "software": "Development", "hardware": "Development", "ai": "Development",
            "docker": "Development", "kubernetes": "Development", "linux": "Development", "github": "Development",
            "science": "Science & Math", "math": "Science & Math", "news": "News",
            "politics": "Government", "president": "Government", "government": "Government",
            "diwali": "Government", "deepavali": "Government", "holiday": "Government",
            "prime": "Government", "minister": "Government",
            "opinions": "Personality", "people": "Social",
            "tweet": "Social", "tweets": "Social", "post": "Social", "posts": "Social", "truth": "Social",
            "trending": "News", "trend": "News", "viral": "News",
            "friends": "Social", "gadgets": "Phone", "devices": "Phone", "phone": "Phone",
            "smartphone": "Phone", "mobile": "Phone", "iphone": "Phone", "samsung": "Phone",
            "pixel": "Phone", "xiaomi": "Phone", "oneplus": "Phone", "oppo": "Phone", "vivo": "Phone",
            "nothing": "Phone",
            "animals": "Animals", "pets": "Animals", "wildlife": "Animals",
            "birds": "Animals", "fish": "Animals", "space": "Science & Math",
            "astronomy": "Science & Math", "physics": "Science & Math",
            "biology": "Science & Math", "chemistry": "Science & Math",
            "medicine": "Health", "doctor": "Health", "hospital": "Health",
            "games": "Games", "gaming": "Games", "movies": "Entertainment", "movie": "Entertainment",
            "films": "Entertainment", "film": "Entertainment", "cinema": "Entertainment", "review": "Entertainment",
            "bollywood": "Entertainment", "hollywood": "Entertainment", "box": "Entertainment",
            "office": "Entertainment", "release": "Entertainment", "releases": "Entertainment",
            "music": "Music", "songs": "Music", "weather": "Weather", "forecast": "Weather",
            "score": "Sports & Fitness", "scores": "Sports & Fitness", "match": "Sports & Fitness",
            "cricket": "Sports & Fitness", "football": "Sports & Fitness", "soccer": "Sports & Fitness",
            "basketball": "Sports & Fitness", "nba": "Sports & Fitness", "nfl": "Sports & Fitness",
            "ipl": "Sports & Fitness",
            "axolotl": "Animals", "axolotls": "Animals"
        }
        
        words = b_low.split()
        for word in words:
            if word in aliases:
                target_cat = aliases[word]
                if target_cat in self.categories:
                    return target_cat

        # 3. Vector Semantic Match (Fallback for unique/complex terms)
        if not self.category_vectors:
            return None

        # Use pre-computed vector if available, otherwise compute
        q_vec = query_vector if query_vector is not None else self._embed(bucket_name, task_type="search_query")
        if q_vec is None: return None
        
        best_cat = None
        best_sim = -1.0
        
        for cat, v in self.category_vectors.items():
            if v is None: continue
            norm_q = np.linalg.norm(q_vec)
            norm_v = np.linalg.norm(v)
            sim = float(np.dot(q_vec, v) / (norm_q * norm_v)) if norm_q > 0 and norm_v > 0 else 0.0
            if sim > best_sim:
                best_sim = sim
                best_cat = cat
                
        if best_sim > 0.82:
            logger.info(f"🎯 Mapped '{bucket_name}' -> Category '{best_cat}' (sim: {best_sim:.2f})")
            return best_cat
        return None

    def get_working_apis(self, category: str, count: int = 3) -> list:
        """Audits APIs in the category actively and returns working endpoints."""
        if category not in self.categories:
            return []
            
        candidates = self.categories[category]
        working = []
        
        for api in candidates:
            if len(working) >= count:
                break
                
            # Perform Live Audit
            try:
                # 3-second timeout to fast-fail dead APIs
                req = requests.get(api["url"], timeout=3.0, headers={"User-Agent": "Mozilla/5.0"})
                if req.status_code == 200:
                    working.append(api)
                else:
                    logger.debug(f"⚠️ APIScout: API {api['name']} audited failed ({req.status_code})")
            except Exception:
                pass
                
        return working

    def _extract_facts_from_json(self, raw_json: str, topic: str) -> str:
        """Pipes raw arbitrary API JSON schemas through llama3.2 to extract usable English facts."""
        # 🟢 Pre-filter JSON to find obvious content fields (save LLM context noise)
        try:
            import json
            data = json.loads(raw_json)
            # Find all strings that look like news/content (heuristic: >20 chars)
            snippets = []
            def _find_strings(obj):
                if isinstance(obj, str):
                    if len(obj) > 20 and not obj.startswith("http"):
                        snippets.append(obj[:200])
                elif isinstance(obj, dict):
                    for k, v in obj.items():
                        # Prioritize titles and descriptions
                        if k.lower() in ["title", "description", "snippet", "content", "summary", "text"]:
                            if isinstance(v, str): snippets.append(v[:300])
                        else:
                            _find_strings(v)
                elif isinstance(obj, list):
                    for item in obj[:10]: _find_strings(item)
            
            _find_strings(data)
            content_sample = "\n".join(snippets[:10])
        except Exception:
            content_sample = raw_json[:2000]

        prompt = (
            f"Extract up to 3 interesting, clear, and concise facts about {topic} from this content. "
            f"Return ONLY pure text facts, separated by periods. No intro, no JSON syntax.\n\n"
            f"CONTENT:\n{content_sample}"
        )
        
        payload = {
            "model": "llama3.2:1b",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 100}
        }
        try:
            # Note: Hardcoded Ollama URL for internal hub usage
            res = requests.post("http://localhost:11434/api/chat", json=payload, timeout=20.0)
            text = res.json().get("message", {}).get("content", "").strip()
            return text
        except Exception as e:
            logger.warning(f"⚠️ APIScout: LLM JSON extraction failed: {e}")
            return ""

    def _normalize_query(self, query: str) -> str:
        q = re.sub(r"\s+", " ", query.lower()).strip()
        typo_map = {
            " trum ": " trump ",
            " twet ": " tweet ",
            " wheather ": " weather ",
            " i phone ": " iphone ",
        }
        padded = f" {q} "
        for wrong, right in typo_map.items():
            padded = padded.replace(wrong, right)
        return padded.strip()

    def _extract_weather_location(self, query: str) -> str | None:
        q = self._normalize_query(query)
        match = re.search(
            r"\b(?:weather|wheather|temperature|forecast)\b.*?\bin\s+([a-zA-Z][a-zA-Z\s.\-]{1,60}?)(?:\s+\b(?:now|today|currently)\b)?[?.! ]*$",
            q,
        )
        if match:
            return match.group(1).strip(" ?.!").title()
        return None

    def _is_bitcoin_price_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return ("btc" in q or "bitcoin" in q) and any(
            word in q for word in ["price", "live", "current", "usd", "value"]
        )

    def _is_crypto_price_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        price_words = ["price", "live", "current", "value", "rate", "trading"]
        crypto_words = [
            "crypto", "cryptocurrency", "coin", "token", "btc", "bitcoin", "eth",
            "ethereum", "sol", "solana", "doge", "dogecoin", "xrp"
        ]
        return any(word in q for word in price_words) and any(word in q for word in crypto_words)

    def _is_crypto_market_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return any(word in q for word in ["crypto", "cryptocurrency", "coin", "token"]) and any(
            word in q for word in ["trending", "trend", "gainer", "gainers", "loser", "movers", "market"]
        )

    def _is_top_news_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return "news" in q and any(word in q for word in ["top", "today", "latest", "headline", "headlines"])

    def _is_current_us_president_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return (
            "president" in q
            and any(word in q for word in ["america", "united states", "usa"])
            and any(phrase in q for phrase in ["who is", "current", "right now", "today"])
        )

    def _is_current_india_pm_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return (
            "prime minister" in q
            and "india" in q
            and any(phrase in q for phrase in ["who is", "current", "right now", "today"])
        )

    def _is_social_post_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        social_words = ["tweet", "post", "truth", "truth social", "social media"]
        return (
            any(word in q for word in social_words)
            and any(word in q for word in social_words)
            and any(word in q for word in ["latest", "last", "recent", "write", "what", "show"])
            and (" by " in f" {q} " or " from " in f" {q} ")
        )

    def _parse_social_post_query(self, query: str) -> tuple[str | None, str | None]:
        q = self._normalize_query(query)
        subject = None
        topic = None

        subject_match = re.search(
            r"\b(?:tweet|post|truth|statement)\b.*?\b(?:by|from)\s+([a-z0-9_.\- ]{2,50}?)(?:\s+\bon\b|\s+\babout\b|$)",
            q,
        )
        if subject_match:
            subject = subject_match.group(1).strip()

        topic_match = re.search(r"\b(?:on|about)\s+(.+)$", q)
        if topic_match:
            topic = topic_match.group(1).strip(" ?.!")

        return subject, topic

    def _run_ddgs_queries(self, queries: list[str], mode: str = "news", max_results: int = 5) -> list[tuple[str, str, str]]:
        snippets = []
        DDGS = None
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except ImportError:
                DDGS = None

        if DDGS is None:
            logger.warning("⚠️ APIScout DDGS search unavailable: install `ddgs` or `duckduckgo-search`.")
            return snippets

        try:
            with DDGS() as ddgs:
                for search_query in queries:
                    iterator = (
                        ddgs.news(
                            search_query,
                            region="us-en",
                            safesearch="moderate",
                            backend="google,duckduckgo,brave,yahoo",
                            max_results=max_results,
                        )
                        if mode == "news"
                        else ddgs.text(
                            search_query,
                            region="us-en",
                            safesearch="moderate",
                            backend="google,duckduckgo,brave,yahoo",
                            max_results=max_results,
                        )
                    )
                    for item in iterator:
                        title = (item.get("title") or "").strip()
                        body = (item.get("body") or item.get("snippet") or "").strip()
                        url = (item.get("url") or item.get("href") or "").strip()
                        if not title and not body:
                            continue
                        snippets.append((title, body, url))
        except Exception as e:
            logger.warning(f"⚠️ APIScout DDGS {mode} search failed: {e}")
        return snippets

    def _handle_social_post_query(self, query: str) -> str | None:
        subject, topic = self._parse_social_post_query(query)
        if not subject:
            return None

        search_queries = [
            f"{subject} latest post {topic or ''}".strip(),
            f"{subject} latest tweet {topic or ''}".strip(),
            f"{subject} {topic or ''} Reuters".strip(),
            f"{subject} {topic or ''} social media".strip(),
        ]
        snippets = self._run_ddgs_queries(search_queries, mode="news", max_results=4)
        if not snippets:
            snippets = self._run_ddgs_queries(search_queries, mode="text", max_results=4)

        if not snippets:
            return None

        top_title, top_body, top_url = snippets[0]
        body = top_body or top_title
        if len(body) > 220:
            body = body[:217].rstrip() + "..."

        return (
            f"--- LIVE WEB ({datetime.now().strftime('%B %d, %Y')}) ---\n"
            f"• Recent reporting about {subject}'s post/message says: {body}\n"
            f"• The exact latest social post was not directly verified.\n"
            f"• Source: {top_url or 'Live web results'}."
        )

    def _is_live_score_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return any(term in q for term in ["live score", "score", "scores", "match score", "result"]) and any(
            term in q for term in ["match", "vs", "v ", "game", "final", "ipl", "nba", "nfl", "cricket", "football", "soccer"]
        )

    def _handle_live_score_query(self, query: str) -> str | None:
        search_queries = [
            self._normalize_query(query),
            f"{self._normalize_query(query)} live score",
        ]
        snippets = self._run_ddgs_queries(search_queries, mode="news", max_results=5)
        if not snippets:
            snippets = self._run_ddgs_queries(search_queries, mode="text", max_results=5)
        if not snippets:
            return None

        lines = []
        for title, body, url in snippets[:3]:
            snippet = body or title
            if len(snippet) > 140:
                snippet = snippet[:137].rstrip() + "..."
            lines.append(f"• {snippet}")
        lines.append(f"• Source: {snippets[0][2] or 'Live web results'}.")
        return f"--- LIVE WEB ({datetime.now().strftime('%B %d, %Y')}) ---\n" + "\n".join(lines)

    def _is_trending_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return any(term in q for term in ["trending", "trends", "what's trending", "what is trending", "trend now"])

    def _is_schedule_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return any(term in q for term in ["schedule", "fixture", "fixtures", "timetable"]) and any(
            term in q for term in ["ipl", "cricket", "series", "league"]
        )

    def _is_holiday_date_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        if any(term in q for term in ["stock", "price", "score", "weather", "review"]):
            return False
        return any(term in q for term in ["when is", "date of", "when's"]) and any(
            term in q for term in ["this year", "festival", "holiday", "diwali", "deepavali", "christmas", "holi", "dussehra", "eid"]
        )

    def _is_movie_review_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return "review" in q and not any(
            term in q for term in ["stock", "crypto", "weather", "news", "match", "schedule", "president"]
        )

    def _handle_trending_query(self, query: str) -> str | None:
        q = self._normalize_query(query)
        search_queries = [
            q,
            "trending topics today",
            "latest trending news today",
        ]
        snippets = self._run_ddgs_queries(search_queries, mode="news", max_results=5)
        if not snippets:
            return None

        lines = []
        for title, body, _ in snippets[:3]:
            item = title or body
            if len(item) > 140:
                item = item[:137].rstrip() + "..."
            lines.append(f"• {item}")
        lines.append("• Source: live news search.")
        return f"--- LIVE WEB ({datetime.now().strftime('%B %d, %Y')}) ---\n" + "\n".join(lines)

    def _is_detailed_news_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return "news" in q and any(term in q for term in ["detail", "details", "detailed", "explain", "what happened", "full update"])

    def _is_stock_price_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        price_words = ["price", "share price", "stock price", "quote", "trading", "live", "current", "value"]
        market_words = ["stock", "share", "shares", "nse", "bse", "nasdaq", "nyse", "sensex", "nifty", "market"]
        return any(word in q for word in price_words) and any(word in q for word in market_words)

    def _is_latest_phone_model_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        freshness_terms = ["latest", "newest", "current", "recent"]
        phone_terms = ["iphone", "phone", "smartphone", "samsung", "pixel", "mobile", "xiaomi", "oneplus", "oppo", "vivo", "nothing"]
        model_terms = ["model", "phone", "smartphone", "mobile", "flagship", "series"]
        return (
            any(term in q for term in freshness_terms)
            and any(term in q for term in phone_terms)
            and any(term in q for term in model_terms)
        )

    def _is_technology_news_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        tech_terms = ["technology", "tech", "software", "hardware", "ai", "artificial intelligence", "startup", "open source", "programming", "developer"]
        news_terms = ["news", "latest", "today", "update", "updates", "headline", "headlines", "trending"]
        return any(term in q for term in tech_terms) and any(term in q for term in news_terms)

    def _is_technology_fact_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        fact_terms = ["what is", "tell me about", "explain", "facts", "fact", "overview"]
        tech_terms = ["technology", "tech", "software", "hardware", "ai", "artificial intelligence", "docker", "kubernetes", "python", "linux", "github", "open source"]
        return any(term in q for term in fact_terms) and any(term in q for term in tech_terms)

    def _is_weather_forecast_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return "forecast" in q or "tomorrow" in q or "next" in q

    def _is_entertainment_query(self, query: str) -> bool:
        q = self._normalize_query(query)
        return any(word in q for word in ["bollywood", "hollywood", "movie", "movies", "film", "films", "box office", "release", "review"])

    def _extract_crypto_asset(self, query: str) -> str | None:
        q = self._normalize_query(query)
        alias_map = {
            "btc": "bitcoin",
            "eth": "ethereum",
            "sol": "solana",
            "doge": "dogecoin",
        }
        for alias, canonical in alias_map.items():
            if re.search(rf"\b{re.escape(alias)}\b", q):
                return canonical

        cleaned = re.sub(
            r"\b(what|is|the|price|live|current|value|rate|of|crypto|cryptocurrency|coin|token|in|usd|usdt|today|now)\b",
            " ",
            q,
        )
        cleaned = re.sub(r"[^a-z0-9\s.-]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or None

    def _extract_stock_search_term(self, query: str) -> tuple[str | None, str | None]:
        q = self._normalize_query(query)
        market_hint = None
        if any(word in q for word in ["india", "indian", "nse", "bse", "sensex", "nifty"]):
            market_hint = "IN"
        elif any(word in q for word in ["us", "usa", "united states", "nasdaq", "nyse", "dow"]):
            market_hint = "US"

        cleaned = re.sub(
            r"\b(what|is|the|share|shares|stock|price|quote|live|current|value|of|in|today|now|market|india|indian|us|usa|united states|nse|bse|sensex|nifty|nasdaq|nyse|dow)\b",
            " ",
            q,
        )
        cleaned = re.sub(r"[^a-z0-9.\s&-]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return (cleaned or None, market_hint)

    def _extract_match_teams(self, query: str) -> list[str]:
        q = self._normalize_query(query)
        cleaned = re.sub(r"\b(what|is|the|live|score|of|current|match|game|today|now)\b", " ", q)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        match = re.search(r"\b([a-z][a-z\s]{1,30}?)\s+(?:vs|v)\s+([a-z][a-z\s]{1,30}?)(?:[?.! ]*$)", cleaned)
        if not match:
            return []
        return [match.group(1).strip(), match.group(2).strip()]

    def _parse_officeholder_query(self, query: str) -> tuple[str | None, str | None]:
        q = self._normalize_query(query)
        office = None
        country = None
        if "president" in q:
            office = "president"
        elif "prime minister" in q:
            office = "prime minister"

        country_aliases = {
            "united states": ["america", "united states", "usa", "us"],
            "india": ["india", "indian"],
        }
        for canonical, aliases in country_aliases.items():
            if any(alias in q for alias in aliases):
                country = canonical
                break
        return office, country

    def _extract_holiday_name(self, query: str) -> str | None:
        q = self._normalize_query(query)
        match = re.search(r"\b(?:when is|date of|when's)\s+(.+?)(?:\s+this year|\s+in\s+\d{4}|[?.! ]*$)", q)
        if match:
            holiday = match.group(1).strip()
        else:
            cleaned = re.sub(r"\b(when|is|the|date|of|this|year|holiday|festival|in)\b", " ", q)
            holiday = re.sub(r"\s+", " ", cleaned).strip()
        return holiday or None

    def _holiday_slug_candidates(self, holiday: str) -> list[str]:
        holiday = self._normalize_query(holiday)
        alias_map = {
            "deepavali": "diwali",
            "dipawali": "diwali",
            "dipavali": "diwali",
        }
        for src, dst in alias_map.items():
            holiday = holiday.replace(src, dst)
        slug = re.sub(r"[^a-z0-9]+", "-", holiday).strip("-")
        candidates = [slug] if slug else []
        if " " in holiday:
            candidates.append(re.sub(r"[^a-z0-9]+", "-", holiday.replace(" ", "")))
        return list(OrderedDict.fromkeys([c for c in candidates if c]))

    def _extract_schedule_competition(self, query: str) -> tuple[str | None, str | None]:
        q = self._normalize_query(query)
        competition = None
        if "ipl" in q or "indian premier league" in q:
            competition = "ipl"
        year_match = re.search(r"\b(20\d{2})\b", q)
        year = year_match.group(1) if year_match else str(datetime.now().year)
        return competition, year

    def _fetch_google_news_rss(self, search_query: str, limit: int = 4) -> list[tuple[str, str]]:
        url = (
            "https://news.google.com/rss/search?q="
            + quote_plus(search_query)
            + "&hl=en-IN&gl=IN&ceid=IN:en"
        )
        try:
            res = requests.get(url, timeout=10.0, headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            root = ET.fromstring(res.text)
            items = []
            for item in root.findall(".//item")[:limit]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if title:
                    items.append((title, link))
            return items
        except Exception as e:
            logger.warning(f"⚠️ APIScout Google News RSS failed for '{search_query}': {e}")
            return []

    def _extract_requested_count(self, query: str, default: int = 3, minimum: int = 1, maximum: int = 10) -> int:
        q = self._normalize_query(query)
        match = re.search(r"\b(\d{1,2})\b", q)
        if match:
            return max(minimum, min(int(match.group(1)), maximum))
        return default

    def _extract_review_subject(self, query: str) -> str | None:
        q = self._normalize_query(query)
        cleaned = re.sub(r"\breview\b", " ", q)
        cleaned = re.sub(r"\b(write|give|show|tell|me|a|an|the|movie|film)\b", " ", cleaned)
        cleaned = re.sub(r"[^a-z0-9\s:&'-]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or None

    def _extract_technology_subject(self, query: str) -> str | None:
        q = self._normalize_query(query)
        cleaned = re.sub(
            r"\b(what|is|tell|me|about|explain|facts|fact|overview|latest|today|news|technology|tech|software|hardware|on|of|the|a|an|for)\b",
            " ",
            q,
        )
        cleaned = re.sub(r"[^a-z0-9\s().:_/-]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or None

    def _extract_phone_brand(self, query: str) -> tuple[str | None, str | None]:
        q = self._normalize_query(query)
        brand_map = {
            "apple": ("Apple", "https://www.gsmarena.com/apple-phones-48.php"),
            "iphone": ("Apple", "https://www.gsmarena.com/apple-phones-48.php"),
            "samsung": ("Samsung", "https://www.gsmarena.com/samsung-phones-9.php"),
            "pixel": ("Google", "https://www.gsmarena.com/google-phones-107.php"),
            "google": ("Google", "https://www.gsmarena.com/google-phones-107.php"),
            "xiaomi": ("Xiaomi", "https://www.gsmarena.com/xiaomi-phones-80.php"),
            "oneplus": ("OnePlus", "https://www.gsmarena.com/oneplus-phones-95.php"),
            "oppo": ("Oppo", "https://www.gsmarena.com/oppo-phones-82.php"),
            "vivo": ("Vivo", "https://www.gsmarena.com/vivo-phones-98.php"),
            "nothing": ("Nothing", "https://www.gsmarena.com/nothing-phones-128.php"),
        }
        for token, config in brand_map.items():
            if token in q:
                return config
        return (None, None)

    def _normalize_indian_date_text(self, value: str) -> str:
        text = str(value).strip()
        month_map = {
            "जनवरी": "January",
            "फ़रवरी": "February",
            "फरवरी": "February",
            "मार्च": "March",
            "अप्रैल": "April",
            "मई": "May",
            "जून": "June",
            "जुलाई": "July",
            "अगस्त": "August",
            "सितंबर": "September",
            "अक्टूबर": "October",
            "नवंबर": "November",
            "दिसंबर": "December",
        }
        weekday_tokens = {"सोम", "मंगल", "बुध", "गुरु", "शुक्र", "शनि", "रवि"}
        parts = [part for part in text.split() if part not in weekday_tokens]
        normalized = " ".join(parts)
        for source, target in month_map.items():
            normalized = normalized.replace(source, target)
        return normalized.strip()

    def _format_live_bullets(self, lines: list[str], source: str) -> str | None:
        clean_lines = [line for line in lines if line]
        if not clean_lines:
            return None
        return (
            f"--- LIVE WEB ({datetime.now().strftime('%B %d, %Y')}) ---\n"
            + "\n".join(f"• {line}" for line in clean_lines)
            + f"\n• Source: {source}."
        )

    def _topic_cache_key(self, topic: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")

    def _format_curated_cache(self, items: list[dict], source_label: str) -> str | None:
        if not items:
            return None
        lines = []
        for item in items:
            title = (item.get("title") or "").strip()
            source = (item.get("source_name") or "").strip()
            if not title:
                continue
            if source:
                lines.append(f"{title} ({source})")
            else:
                lines.append(title)
        return self._format_live_bullets(lines, source_label) if lines else None

    def _handle_detailed_news_query(self, query: str) -> str | None:
        cleaned = self._normalize_query(query)
        for phrase in ["news", "in detail", "detailed", "details", "detail", "what happened", "full update", "explain"]:
            cleaned = cleaned.replace(phrase, " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if self.current_user and self.current_user != "guest":
            cached_user = self.db.get_curated_topic("user", "News", self._topic_cache_key(cleaned or query), user_id=self.current_user, limit=4)
            if cached_user:
                return self._format_curated_cache(cached_user, "OpenClaw curated user cache")
        cached = self.db.get_curated_topic("global", "News", self._topic_cache_key(cleaned or query), limit=4)
        if cached:
            return self._format_curated_cache(cached, "OpenClaw curated cache")
        items = self._fetch_google_news_rss(cleaned or self._normalize_query(query), limit=4)
        if not items:
            return None
        lines = [title for title, _ in items[:3]]
        return self._format_live_bullets(lines, "Google News RSS")

    def _resolve_coingecko_coin(self, asset: str) -> tuple[str, str] | None:
        alias_map = {
            "bitcoin": ("bitcoin", "Bitcoin"),
            "ethereum": ("ethereum", "Ethereum"),
            "solana": ("solana", "Solana"),
            "dogecoin": ("dogecoin", "Dogecoin"),
            "xrp": ("ripple", "XRP"),
        }
        if asset in alias_map:
            return alias_map[asset]

        try:
            res = requests.get(
                "https://api.coingecko.com/api/v3/search",
                params={"query": asset},
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            res.raise_for_status()
            coins = res.json().get("coins", [])
            if not coins:
                return None
            top = sorted(coins, key=lambda item: item.get("market_cap_rank") or 10**9)[0]
            return top.get("id"), top.get("name")
        except Exception as e:
            logger.warning(f"⚠️ APIScout CoinGecko search failed for '{asset}': {e}")
            return None

    def _handle_crypto_price_query(self, query: str) -> str | None:
        asset = self._extract_crypto_asset(query)
        if not asset:
            return None
        resolved = self._resolve_coingecko_coin(asset)
        if not resolved:
            return None
        coin_id, coin_name = resolved
        urls = [
            ("CoinGecko", "https://api.coingecko.com/api/v3/simple/price", {"ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true"}),
        ]
        for source, url, params in urls:
            try:
                res = requests.get(url, params=params, timeout=8.0, headers={"User-Agent": "Mozilla/5.0"})
                res.raise_for_status()
                data = res.json()
                coin = data.get(coin_id, {})
                price = coin.get("usd")
                change = coin.get("usd_24h_change")
                if price is not None:
                    lines = [f"{coin_name} is trading at ${float(price):,.2f} USD right now"]
                    if change is not None:
                        lines.append(f"24-hour change: {float(change):.2f}%")
                    return self._format_live_bullets(lines, source)
            except Exception as e:
                logger.warning(f"⚠️ APIScout crypto handler failed via {source}: {e}")
        return None

    def _handle_crypto_market_query(self) -> str | None:
        try:
            res = requests.get(
                "https://api.coingecko.com/api/v3/search/trending",
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            res.raise_for_status()
            coins = res.json().get("coins", [])
            lines = []
            for row in coins[:3]:
                item = row.get("item", {})
                name = item.get("name")
                symbol = item.get("symbol")
                rank = item.get("market_cap_rank")
                if name:
                    suffix = f" ({symbol})" if symbol else ""
                    rank_text = f", market-cap rank {rank}" if rank else ""
                    lines.append(f"{name}{suffix}{rank_text}")
            return self._format_live_bullets(lines, "CoinGecko trending")
        except Exception as e:
            logger.warning(f"⚠️ APIScout crypto trending handler failed: {e}")
            return None

    def _handle_technology_news_query(self, query: str) -> str | None:
        q = self._normalize_query(query)
        subject = self._extract_technology_subject(q)
        params = {"tags": "story", "hitsPerPage": 5}
        endpoint = "https://hn.algolia.com/api/v1/search_by_date"
        if subject:
            params["query"] = subject
        else:
            endpoint = "https://hn.algolia.com/api/v1/search"
            params["tags"] = "front_page"
        try:
            res = requests.get(
                endpoint,
                params=params,
                timeout=10.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            res.raise_for_status()
            hits = res.json().get("hits", [])
            lines = []
            for item in hits[:5]:
                title = (item.get("title") or item.get("story_title") or "").strip()
                if not title:
                    continue
                points = item.get("points")
                suffix = f" ({points} points)" if points is not None else ""
                lines.append(f"{title}{suffix}")
            if not lines:
                return None
            return self._format_live_bullets(lines, "HN Algolia API")
        except Exception as e:
            logger.warning(f"⚠️ APIScout technology news handler failed: {e}")
            return None

    def _handle_technology_fact_query(self, query: str) -> str | None:
        subject = self._extract_technology_subject(query)
        if not subject:
            return None
        try:
            q = self._normalize_query(query)
            title_candidates = []
            if any(token in q for token in ["software", "platform", "framework", "tool", "technology", "tech"]):
                title_candidates.extend([
                    f"{subject} (software)",
                    f"{subject} (computing)",
                ])
            title_candidates.append(subject)

            summary = None
            page_title = None
            for candidate in title_candidates:
                summary_res = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(candidate.replace(' ', '_'))}",
                    timeout=10.0,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if summary_res.status_code != 200:
                    continue
                candidate_summary = summary_res.json()
                extract = (candidate_summary.get("extract") or "").strip()
                if extract and "most often refers to" not in extract.lower():
                    summary = candidate_summary
                    page_title = candidate_summary.get("title") or candidate
                    break

            search_res = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "opensearch",
                    "search": subject,
                    "limit": 1,
                    "namespace": 0,
                    "format": "json",
                },
                timeout=10.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            search_res.raise_for_status()
            if summary is None:
                data = search_res.json()
                titles = data[1] if len(data) > 1 else []
                if not titles:
                    return None
                def score(title: str) -> int:
                    lower = title.lower()
                    points = 0
                    if any(token in q for token in ["software", "tech", "technology", "programming", "open source"]):
                        if "(software)" in lower:
                            points += 5
                        if any(token in lower for token in ["software", "framework", "programming", "platform"]):
                            points += 3
                    if "disambiguation" in lower:
                        points -= 10
                    return points

                title = sorted(titles, key=score, reverse=True)[0].replace(" ", "_")
                summary_res = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title)}",
                    timeout=10.0,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                summary_res.raise_for_status()
                summary = summary_res.json()
                page_title = summary.get("title") or title.replace("_", " ")

            extract = (summary.get("extract") or "").strip()
            if not extract:
                return None
            sentences = re.split(r"(?<=[.!?])\s+", extract)
            lines = [f"{page_title}: {sentences[0]}"]
            if len(sentences) > 1 and sentences[1]:
                lines.append(sentences[1])
            return self._format_live_bullets(lines, "Wikipedia API")
        except Exception as e:
            logger.warning(f"⚠️ APIScout technology fact handler failed: {e}")
            return None

    def _handle_weather_query(self, location: str) -> str | None:
        try:
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": location,
                    "count": 1,
                    "language": "en",
                    "format": "json",
                },
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            geo.raise_for_status()
            results = geo.json().get("results", [])
            if not results:
                return None

            place = results[0]
            forecast = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,wind_speed_10m,weather_code",
                    "timezone": "auto",
                },
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            forecast.raise_for_status()
            current = forecast.json().get("current", {})
            temp = current.get("temperature_2m")
            wind = current.get("wind_speed_10m")
            if temp is None and wind is None:
                return None

            parts = []
            if temp is not None:
                parts.append(f"• Current temperature in {place['name']}, {place.get('admin1', place.get('country', ''))}: {round(float(temp))}°C.")
            if wind is not None:
                parts.append(f"• Wind speed: {round(float(wind))} km/h.")
            parts.append("• Source: Open-Meteo.")
            return f"--- LIVE WEB ({datetime.now().strftime('%B %d, %Y')}) ---\n" + "\n".join(parts)
        except Exception as e:
            logger.warning(f"⚠️ APIScout weather handler failed for '{location}': {e}")
            return None

    def _handle_weather_forecast_query(self, location: str) -> str | None:
        try:
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en", "format": "json"},
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            geo.raise_for_status()
            results = geo.json().get("results", [])
            if not results:
                return None
            place = results[0]
            forecast = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto",
                    "forecast_days": 2,
                },
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            forecast.raise_for_status()
            daily = forecast.json().get("daily", {})
            dates = daily.get("time", [])
            maxes = daily.get("temperature_2m_max", [])
            mins = daily.get("temperature_2m_min", [])
            if len(dates) < 2:
                return None
            return self._format_live_bullets(
                [
                    f"Forecast for {place['name']}, {place.get('admin1', place.get('country', ''))} on {dates[1]}",
                    f"High: {round(float(maxes[1]))}°C",
                    f"Low: {round(float(mins[1]))}°C",
                ],
                "Open-Meteo",
            )
        except Exception as e:
            logger.warning(f"⚠️ APIScout weather forecast handler failed for '{location}': {e}")
            return None

    def _handle_officeholder_query(self, query: str) -> str | None:
        office, country = self._parse_officeholder_query(query)
        if not office or not country:
            return None
        config = OFFICEHOLDER_QUERIES.get((office, country))
        if not config:
            return None
        try:
            res = requests.get(
                "https://query.wikidata.org/sparql",
                params={"format": "json", "query": config["query"]},
                headers={
                    "Accept": "application/sparql-results+json",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=15.0,
            )
            res.raise_for_status()
            bindings = res.json().get("results", {}).get("bindings", [])
            if not bindings:
                return None
            person = bindings[0].get("personLabel", {}).get("value")
            if not person:
                return None
            lines = [f"The current {config['label']} is {person}"]
            start = bindings[0].get("start", {}).get("value", "")[:10]
            if start:
                lines.append(f"Term start date: {start}")
            return self._format_live_bullets(lines, config["source"])
        except Exception as e:
            logger.warning(f"⚠️ APIScout officeholder handler failed for '{office} {country}': {e}")
            return None

    def _resolve_yahoo_symbol(self, search_term: str, market_hint: str | None) -> tuple[str, str, str] | None:
        try:
            res = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={
                    "q": search_term,
                    "quotesCount": 8,
                    "newsCount": 0,
                    "lang": "en-US",
                    "region": "US" if market_hint != "IN" else "IN",
                },
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            res.raise_for_status()
            quotes = res.json().get("quotes", [])
            if not quotes:
                return None

            def score(item: dict) -> int:
                symbol = (item.get("symbol") or "").upper()
                exch = (item.get("exchange") or "").upper()
                score_value = 0
                if market_hint == "IN":
                    if symbol.endswith(".NS") or symbol.endswith(".BO"):
                        score_value += 5
                    if exch in {"NSI", "BSE", "BOM"}:
                        score_value += 4
                elif market_hint == "US":
                    if exch in {"NMS", "NYQ", "NGM", "ASE", "PCX"}:
                        score_value += 4
                    if not symbol.endswith((".NS", ".BO")):
                        score_value += 2
                if item.get("quoteType") == "EQUITY":
                    score_value += 3
                return score_value

            top = sorted(quotes, key=score, reverse=True)[0]
            symbol = top.get("symbol")
            name = top.get("shortname") or top.get("longname") or symbol
            exchange = top.get("exchangeDisplay") or top.get("exchange") or "Yahoo Finance"
            if not symbol:
                return None
            return symbol, name, exchange
        except Exception as e:
            logger.warning(f"⚠️ APIScout Yahoo symbol search failed for '{search_term}': {e}")
            return None

    def _handle_stock_price_query(self, query: str) -> str | None:
        search_term, market_hint = self._extract_stock_search_term(query)
        if not search_term:
            return None
        resolved = self._resolve_yahoo_symbol(search_term, market_hint)
        if not resolved:
            return None
        symbol, name, exchange = resolved
        try:
            res = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1d", "range": "1d"},
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            res.raise_for_status()
            result = (res.json().get("chart", {}).get("result") or [])
            if not result:
                return None
            meta = result[0].get("meta", {})
            price = meta.get("regularMarketPrice") or meta.get("previousClose")
            currency = meta.get("currency") or ""
            previous_close = meta.get("previousClose")
            if price is None:
                return None
            lines = [f"{name} ({symbol}) is trading at {price} {currency}".strip()]
            if previous_close not in (None, 0) and price is not None:
                change = ((float(price) - float(previous_close)) / float(previous_close)) * 100.0
                lines.append(f"Day change: {change:.2f}%")
            lines.append(f"Exchange: {exchange}")
            return self._format_live_bullets(lines, "Yahoo Finance")
        except Exception as e:
            logger.warning(f"⚠️ APIScout Yahoo quote failed for '{symbol}': {e}")
            return None

    def _handle_latest_phone_model_query(self, query: str) -> str | None:
        brand, source_url = self._extract_phone_brand(query)
        if not brand or not source_url:
            return None
        sources = [
            ("GSMArena", source_url),
        ]
        if brand == "Apple":
            sources.append(("Apple Compare", "https://www.apple.com/iphone/compare/"))

        for source_name, url in sources:
            try:
                res = requests.get(url, timeout=15.0, headers={"User-Agent": "Mozilla/5.0"})
                res.raise_for_status()
                html = res.text
                if source_name == "GSMArena":
                    names = [name.strip() for name in re.findall(r"<span>([^<]+)</span>", html) if name.strip()]
                else:
                    names = [name.replace("\xa0", " ").strip() for name in re.findall(r"iPhone\s+[0-9]{2}\s*(?:Pro Max|Pro|Plus|e)?", html)]
                phone_names = []
                for name in names:
                    lowered = name.lower()
                    if brand == "Apple" and lowered.startswith("iphone"):
                        phone_names.append(name)
                    elif brand == "Samsung" and ("galaxy" in lowered or lowered.startswith("samsung")):
                        phone_names.append(name)
                    elif brand == "Google" and lowered.startswith("pixel"):
                        phone_names.append(name)
                    elif brand == "Xiaomi" and ("xiaomi" in lowered or lowered.startswith("redmi")):
                        phone_names.append(name)
                    elif brand == "OnePlus" and lowered.startswith("oneplus"):
                        phone_names.append(name)
                    elif brand == "Oppo" and lowered.startswith("oppo"):
                        phone_names.append(name)
                    elif brand == "Vivo" and lowered.startswith("vivo"):
                        phone_names.append(name)
                    elif brand == "Nothing" and lowered.startswith("phone"):
                        phone_names.append(name)
                if not phone_names:
                    continue
                latest = phone_names[0]
                lines = [f"The latest {brand} phone model listed is {latest}"]
                siblings = [name for name in phone_names[1:4] if name != latest]
                if siblings:
                    lines.append(f"Other recent models: {', '.join(siblings)}")
                return self._format_live_bullets(lines, source_name)
            except Exception as e:
                logger.warning(f"⚠️ APIScout phone model handler failed via {source_name}: {e}")
        return None

    def _handle_top_news_query(self, query: str | None = None) -> str | None:
        cached = self.db.get_curated_topic("global", "News", "top_today", limit=self._extract_requested_count(query or "", default=3, minimum=3, maximum=10))
        if cached:
            return self._format_curated_cache(cached, "OpenClaw curated cache")
        try:
            res = requests.get(
                "https://ok.surf/api/v1/cors/news-feed",
                timeout=10.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            res.raise_for_status()
            data = res.json()

            preferred_sections = ["US", "World", "Business", "Technology", "Science", "Sports"]
            limit = self._extract_requested_count(query or "", default=3, minimum=3, maximum=10)
            headlines = OrderedDict()
            for section in preferred_sections:
                for item in data.get(section, []):
                    title = (item.get("title") or "").strip()
                    source = (item.get("source") or "").strip()
                    if title and title not in headlines:
                        headlines[title] = source or section
                    if len(headlines) >= limit:
                        break
                if len(headlines) >= limit:
                    break

            if not headlines:
                return None

            lines = [
                f"{title} ({source})"
                for title, source in list(headlines.items())[:limit]
            ]
            return self._format_live_bullets(lines, "ok.surf news feed")
        except Exception as e:
            logger.warning(f"⚠️ APIScout news handler failed: {e}")
            return None

    def _handle_entertainment_query(self, query: str) -> str | None:
        q = self._normalize_query(query)
        if self._is_movie_review_query(q):
            subject = self._extract_review_subject(q)
            if not subject:
                return None
            items = self._fetch_google_news_rss(f"{subject} review", limit=5)
            if not items:
                return None
            return self._format_live_bullets([title for title, _ in items[:3]], "Google News RSS")

        topic = "Bollywood" if "bollywood" in q else "Hollywood" if "hollywood" in q else "movie"
        if "box office" in q:
            topic = f"{topic} box office"
        elif "release" in q:
            topic = f"{topic} releases"
        items = self._fetch_google_news_rss(topic, limit=4)
        if not items:
            return None
        return self._format_live_bullets([title for title, _ in items[:3]], "Google News RSS")

    def _handle_live_score_query(self, query: str) -> str | None:
        teams = self._extract_match_teams(query)
        try:
            res = requests.get(
                "https://www.cricbuzz.com/cricket-match/live-scores",
                timeout=10.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            res.raise_for_status()
            text = re.sub(r"<[^>]+>", " ", res.text)
            text = re.sub(r"\s+", " ", text)
            matches = re.findall(r"([A-Za-z][A-Za-z\s&.-]{3,80}?\s+vs\s+[A-Za-z][A-Za-z\s&.-]{2,80}?\s*-\s*[^<]{3,120})", text)
            if not matches:
                return None

            if teams:
                team_words = [team.lower() for team in teams]
                for match_line in matches:
                    line = match_line.strip()
                    lower_line = line.lower()
                    if all(team_word in lower_line for team_word in team_words):
                        return self._format_live_bullets([line], "Cricbuzz")
                return self._format_live_bullets(
                    [f"No live cricket score found right now for {teams[0].title()} vs {teams[1].title()}"],
                    "Cricbuzz",
                )

            return self._format_live_bullets([matches[0].strip()], "Cricbuzz")
        except Exception as e:
            logger.warning(f"⚠️ APIScout live score handler failed: {e}")
            return None

    def _handle_sports_schedule_query(self, query: str) -> str | None:
        competition, year = self._extract_schedule_competition(query)
        if not competition:
            return None
        config = SPORTS_SCHEDULE_SOURCES.get(competition)
        if not config:
            return None
        urls = config["urls"](year)
        text = None
        for url in urls:
            try:
                res = requests.get(url, timeout=12.0, headers={"User-Agent": "Mozilla/5.0"})
                if res.status_code == 200 and config["label"] in res.text:
                    text = res.text
                    break
            except Exception as e:
                logger.warning(f"⚠️ APIScout sports schedule fetch failed via {url}: {e}")
        if not text:
            return None

        fixtures = []
        needle = 'matchDetailsMap\\":{\\"key\\":\\"'
        search_from = 0
        while len(fixtures) < 5:
            idx = text.find(needle, search_from)
            if idx == -1:
                break
            window = text[idx:idx + 3200]
            search_from = idx + len(needle)

            series_match = re.search(r'seriesName\\":\\"([^\\"]+)\\"', window)
            desc_match = re.search(r'matchDesc\\":\\"([^\\"]+)\\"', window)
            start_match = re.search(r'startDate\\":\\"?(\d+)\\"?', window)
            teams = re.findall(r'teamName\\":\\"([^\\"]+)\\"', window)
            ground_match = re.search(r'ground\\":\\"([^\\"]+)\\"', window)
            city_match = re.search(r'city\\":\\"([^\\"]+)\\"', window)

            if not (
                series_match and series_match.group(1) == f"{config['label']} {year}"
                and desc_match and start_match and len(teams) >= 2 and ground_match and city_match
            ):
                continue

            start_dt = datetime.fromtimestamp(int(start_match.group(1)) / 1000)
            fixture = (
                f"{start_dt.strftime('%a, %d %b %Y')}: {teams[0]} vs {teams[1]} "
                f"({desc_match.group(1)}, {city_match.group(1)} - {ground_match.group(1)})"
            )
            if fixture not in fixtures:
                fixtures.append(fixture)
        if not fixtures:
            return None
        return self._format_live_bullets(fixtures, config["source"])

    def _handle_holiday_date_query(self, query: str) -> str | None:
        year_match = re.search(r"\b(20\d{2})\b", self._normalize_query(query))
        year = int(year_match.group(1)) if year_match else datetime.now().year
        holiday_name = self._extract_holiday_name(query)
        if not holiday_name:
            return None
        slugs = self._holiday_slug_candidates(holiday_name)
        if not slugs:
            return None
        try:
            for slug in slugs:
                tables = pd.read_html(f"https://www.timeanddate.com/holidays/india/{slug}?year={year}")
                for table in tables:
                    if "Year" in table.columns and "Date" in table.columns:
                        matches = table[table["Year"].astype(str) == str(year)]
                        if not matches.empty:
                            date_value = self._normalize_indian_date_text(matches.iloc[0]["Date"])
                            return self._format_live_bullets(
                                [f"{holiday_name.title()} in {year} falls on {date_value}"],
                                "timeanddate.com",
                            )
                    if table.shape[1] >= 2:
                        left = " ".join(table.iloc[:, 0].astype(str).tolist()).lower()
                        if "this year" in left:
                            this_year_row = self._normalize_indian_date_text(table.iloc[0, 1])
                            return self._format_live_bullets(
                                [f"{holiday_name.title()} in {year} falls on {str(this_year_row).strip()}"],
                                "timeanddate.com",
                            )
        except Exception as e:
            logger.warning(f"⚠️ APIScout holiday date handler failed: {e}")
        return None

    def _execute_category_query(self, category: str, query: str) -> str | None:
        normalized = self._normalize_query(query)

        if category == "Cryptocurrency":
            if self._is_crypto_price_query(normalized) or self._is_bitcoin_price_query(normalized):
                return self._handle_crypto_price_query(normalized)
            if self._is_crypto_market_query(normalized):
                return self._handle_crypto_market_query()

        if category == "Finance":
            if self._is_stock_price_query(normalized):
                return self._handle_stock_price_query(normalized)

        if category == "Phone":
            if self._is_latest_phone_model_query(normalized):
                return self._handle_latest_phone_model_query(normalized)

        if category == "Development":
            if self._is_technology_news_query(normalized):
                return self._handle_technology_news_query(normalized)
            if self._is_technology_fact_query(normalized):
                return self._handle_technology_fact_query(normalized)

        if category == "Weather":
            location = self._extract_weather_location(normalized)
            if location:
                if self._is_weather_forecast_query(normalized):
                    return self._handle_weather_forecast_query(location)
                return self._handle_weather_query(location)

        if category == "Government":
            if self._is_holiday_date_query(normalized):
                return self._handle_holiday_date_query(normalized)
            officeholder = self._handle_officeholder_query(normalized)
            if officeholder:
                return officeholder

        if category == "News":
            if self._is_top_news_query(normalized):
                return self._handle_top_news_query(normalized)
            if self._is_trending_query(normalized):
                return self._handle_top_news_query(normalized)
            if self._is_detailed_news_query(normalized):
                return self._handle_detailed_news_query(normalized)

        if category == "Sports & Fitness":
            if self._is_live_score_query(normalized):
                return self._handle_live_score_query(normalized)
            if self._is_schedule_query(normalized):
                return self._handle_sports_schedule_query(normalized)
            return None

        if category == "Social":
            return None

        if category == "Entertainment":
            if self._is_entertainment_query(normalized):
                return self._handle_entertainment_query(normalized)

        return None

    def execute_query(self, query: str, query_vector=None) -> str:
        """
        Reactive API Path: Maps query to category, fetches data, extracts facts.
        Returns a formatted string of facts or None if no suitable API found.
        """
        # 1. Map Query to Category
        category = self.match_bucket_to_category(query, query_vector=query_vector)
        if not category:
            first_word = query.split()[0]
            category = self.match_bucket_to_category(first_word)
            
        if not category:
            return None
            
        logger.info(f"🚀 APIScout: Executing reactive query for '{query}' in Category '{category}'")

        category_result = self._execute_category_query(category, query)
        if category_result:
            logger.info(f"🚀 APIScout: Category handler matched '{query}' in '{category}'")
            return category_result
        
        # Some categories need a structured category-specific handler. If that
        # did not return usable data, let the router fall back to web search.
        if category in {"News", "Social", "Sports & Fitness", "Government", "Entertainment", "Development", "Phone"}:
            return None

        # 2. Get Working APIs
        working = self.get_working_apis(category, count=2)
        if not working:
            return None
            
        # 3. Call and Extract in Parallel
        def _attempt_api(api):
            try:
                res = requests.get(api["url"], timeout=5.0, headers={'User-Agent': 'Mozilla/5.0'})
                if res.status_code == 200:
                    facts = self._extract_facts_from_json(res.text, query)
                    if facts:
                        return f"--- {api['name']} ({category}) ---\n{facts}"
            except Exception:
                pass
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(working)) as executor:
            future_to_api = {executor.submit(_attempt_api, api): api for api in working}
            for future in concurrent.futures.as_completed(future_to_api):
                result = future.result()
                if result:
                    # Cancel other futures if possible (not really possible with as_completed, 
                    # but we return the first one)
                    return result

        return None

    def lookup_api(self, text: str, query_vector=None, threshold: float = 0.85):
        """Bridge to the new reactive execution path."""
        return self.execute_query(text, query_vector)
