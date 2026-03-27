import threading
import time
import logging
import random
import urllib.request
import urllib.parse
import json
import requests
from bs4 import BeautifulSoup

from core.database_manager import DatabaseManager
from nlu.api_scout import APIScout

logger = logging.getLogger("IdleLibrarian")

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
OLLAMA_CHAT_URL  = "http://localhost:11434/api/chat"
NOMIC_MODEL      = "nomic-embed-text:v1.5"

class IdleLibrarian(threading.Thread):
    def __init__(self, engine_state):
        super().__init__(daemon=True)
        self.state = engine_state
        self.db = DatabaseManager()
        self.scout = APIScout()
        self.running = True
        logger.info("📚 Idle Librarian online. Waiting for engine idle states.")

    def _embed(self, text: str):
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
                return json.loads(res.read().decode('utf-8'))['embedding']
        except Exception as e:
            logger.error(f"⚠️ Nomic embed failed: {e}")
            return None

    # 🟢 Facts now extracted via APIScout central utility

    def fetch_rss_news(self):
        logger.info("📚 Librarian: Harvesting live 'News' APIs via APIScout...")
        working_apis = self.scout.get_working_apis("News", count=3)
        
        for api in working_apis:
            try:
                # Audit pass is 3s, real fetch gets 10s
                res = requests.get(api["url"], timeout=10.0, headers={'User-Agent': 'Mozilla/5.0'})
                if res.status_code == 200:
                    raw_text = res.text
                    facts = self.scout._extract_facts_from_json(raw_text, "Current News")
                    
                    if facts:
                        vec = self._embed(facts)
                        if vec:
                            key = f"news_api_{hash(api['url'])}"
                            self.db.save_hot_cache("News", key, facts, vector=vec, ttl_seconds=3600)
            except Exception as e:
                logger.warning(f"⚠️ Failed to scrape News API {api['name']}: {e}")
            
            time.sleep(random.uniform(2.0, 5.0))

    def fetch_local_context(self):
        logger.info("📚 Librarian: Tracking User Location & Local Weather...")
        
        city = "Guwahati"  # Fallback
        try:
            loc_res = requests.get("http://ip-api.com/json/", timeout=5.0)
            if loc_res.status_code == 200:
                data = loc_res.json()
                city = data.get("city", "Guwahati")
                logger.debug(f"📚 IP-API Located user in: {city}")
        except Exception as e:
            logger.warning(f"⚠️ IP-API failed, falling back to Guwahati: {e}")

        # Weather API Harvesting
        weather_apis = self.scout.get_working_apis("Weather", count=2)
        weather_facts = ""
        
        for api in weather_apis:
            try:
                url = api["url"]
                # Some naive public APIs accept /city paths or ?q=city endpoints. 
                # We'll just append it generically. If it fails, the fallback picks it up.
                if "?" in url: url += f"&q={city}"
                else: url += f"?q={city}"
                
                res = requests.get(url, timeout=5.0)
                if res.status_code == 200:
                    weather_facts = self.scout._extract_facts_from_json(res.text, f"Weather in {city}")
                    if weather_facts: break # Got it
            except Exception:
                continue

        if not weather_facts:
            # Final web scraper fallback if 48-hr GitHub APIs universally fail
            try:
                try:
                    from ddgs import DDGS
                except ImportError:
                    from duckduckgo_search import DDGS
                with DDGS() as ddgs:
                    results = list(
                        ddgs.text(
                            f"current weather in {city}",
                            region="us-en",
                            safesearch="moderate",
                            backend="google,duckduckgo,brave,yahoo",
                            max_results=1,
                        )
                    )
                    if results:
                        weather_facts = results[0].get("body", "")
            except Exception as e:
                logger.warning(f"⚠️ DuckDuckGo Weather Fallback failed: {e}")
                weather_facts = f"The current weather in {city} is mild."

        if weather_facts:
            vec_w = self._embed(weather_facts)
            if vec_w:
                self.db.save_hot_cache("Weather", f"{city}_current", weather_facts, vector=vec_w, ttl_seconds=1800)

    def fetch_bucket_context(self):
        logger.info("📚 Librarian: Fetching Personalized Bucket Context...")
        buckets = self.db.get_all_user_buckets()
        if not buckets:
            return

        for bucket in buckets:
            logger.info(f"📚 Librarian: Contextualizing '{bucket}'...")
            
            # Map Bucket to GitHub APIScout Category
            gh_cat = self.scout.match_bucket_to_category(bucket)
            if gh_cat:
                logger.info(f"📚 APIScout Matched '{bucket}' -> '{gh_cat}'")
                api_list = self.scout.get_working_apis(gh_cat, count=3)
            else:
                api_list = []

            facts = ""
            for api in api_list:
                try:
                    res = requests.get(api["url"], timeout=5.0, headers={'User-Agent': 'Mozilla/5.0'})
                    if res.status_code == 200:
                        facts = self.scout._extract_facts_from_json(res.text, bucket)
                        if facts: break
                except Exception:
                    continue
                    
            # API Backup -> DDGS Fallback
            if not facts:
                logger.info(f"📚 Falling back to DuckDuckGo for bucket '{bucket}'")
                try:
                    try:
                        from ddgs import DDGS
                    except ImportError:
                        from duckduckgo_search import DDGS
                    with DDGS() as ddgs:
                        results = list(
                            ddgs.text(
                                f"latest {bucket} news trends",
                                region="us-en",
                                safesearch="moderate",
                                backend="google,duckduckgo,brave,yahoo",
                                max_results=2,
                            )
                        )
                        for r in results:
                            facts += r.get("body", "") + ". "
                except Exception as e:
                    logger.warning(f"⚠️ DuckDuckGo Bucket Fallback failed: {e}")

            if facts:
                vec = self._embed(facts)
                if vec:
                    key = f"profile_{bucket}_{hash(facts)}"
                    self.db.save_hot_cache(f"Profile_{bucket}", key, facts[:500], vector=vec, ttl_seconds=86400)
            
            time.sleep(random.uniform(5.0, 8.0))

    def is_engine_idle(self):
        modes = self.state.session_mode.values()
        if not modes: return True
        return all(m in ["IDLE", "LISTENING"] for m in modes) and not getattr(self.state, "is_conversation", False)

    def run(self):
        time.sleep(30)
        while self.running:
            if self.is_engine_idle():
                logger.info("📚 Engine is IDLE. Librarian starting maintenance cycle.")
                self.db.clean_expired_cache()
                self.fetch_rss_news()
                time.sleep(random.uniform(5.0, 10.0))
                self.fetch_bucket_context()
                time.sleep(random.uniform(5.0, 10.0))
                self.fetch_local_context()
                logger.info("📚 Librarian maintenance cycle complete.")
            
            sleep_time = random.uniform(1800, 2700)
            target = time.time() + sleep_time
            while time.time() < target and self.running:
                time.sleep(10)
