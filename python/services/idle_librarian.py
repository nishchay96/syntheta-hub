import threading
import time
import logging
import random
import urllib.request
import urllib.parse
import json
import xml.etree.ElementTree as ET
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from core.database_manager import DatabaseManager

logger = logging.getLogger("IdleLibrarian")

OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
NOMIC_MODEL = "nomic-embed-text:v1.5"

RSS_FEEDS = {
    "NYT": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "BBC": "http://feeds.bbci.co.uk/news/rss.xml",
    "TechCrunch": "https://techcrunch.com/feed/"
}

class IdleLibrarian(threading.Thread):
    def __init__(self, engine_state):
        super().__init__(daemon=True)
        self.state = engine_state
        self.db = DatabaseManager()
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

    def _clean_html(self, raw_html):
        if not raw_html: return ""
        if BeautifulSoup:
            return BeautifulSoup(raw_html, "html.parser").get_text(separator=" ").strip()
        else:
            return raw_html.strip()

    def fetch_rss_news(self):
        logger.info("📚 Librarian: Fetching RSS News...")
        for source, url in RSS_FEEDS.items():
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10.0) as response:
                    xml_data = response.read()
                
                root = ET.fromstring(xml_data)
                for item in root.findall('.//item')[:3]:
                    title = item.find('title').text if item.find('title') is not None else ""
                    desc = item.find('description').text if item.find('description') is not None else ""
                    clean_desc = self._clean_html(desc)
                    
                    full_text = f"{title}. {clean_desc}"
                    vec = self._embed(full_text)
                    if vec:
                        # Store in Hot Cache with 1 hour TTL
                        key = f"news_{source}_{hash(title)}"
                        self.db.save_hot_cache("News", key, full_text, vector=vec, ttl_seconds=3600)
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch RSS for {source}: {e}")
            
            # Rate limiting / Jitter between sources
            time.sleep(random.uniform(2.0, 5.0))

    def fetch_local_context(self):
        logger.info("📚 Librarian: Fetching Local Context (Weather/Crypto Trends)...")
        # Dummy implementations that would normally hit an API
        weather_text = "The current weather in Guwahati is 28°C and partly cloudy."
        crypto_text = "Bitcoin is currently trading at $65,000, up 2% in the last 24 hours."

        vec_w = self._embed(weather_text)
        if vec_w:
            self.db.save_hot_cache("Weather", "guwahati_current", weather_text, vector=vec_w, ttl_seconds=1800)
            
        vec_c = self._embed(crypto_text)
        if vec_c:
            self.db.save_hot_cache("Trends", "btc_current", crypto_text, vector=vec_c, ttl_seconds=900)

    def fetch_bucket_context(self):
        logger.info("📚 Librarian: Fetching Personalized Bucket Context...")
        buckets = self.db.get_all_user_buckets()
        if not buckets:
            logger.info("📚 Librarian: No user profile buckets found.")
            return

        for bucket in buckets:
            logger.info(f"📚 Librarian: Fetching context for profile bucket '{bucket}'...")
            try:
                query = f"{bucket} latest news trends overview"
                url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10.0) as response:
                    data = json.loads(response.read().decode('utf-8'))
                
                texts = []
                if data.get("AbstractText"):
                    texts.append(f"Context for {bucket}: {data['AbstractText']}")
                
                for topic in data.get("RelatedTopics", [])[:3]:
                    if isinstance(topic, dict) and topic.get("Text"):
                        texts.append(f"{topic['Text']}")
                
                for text in texts:
                    vec = self._embed(text)
                    if vec:
                        # Store in Hot Cache with 24-hour TTL for deep profiling
                        key = f"profile_{bucket}_{hash(text)}"
                        self.db.save_hot_cache(f"Profile_{bucket}", key, text, vector=vec, ttl_seconds=86400)
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch context for bucket {bucket}: {e}")
            
            # Rate limiting / Jitter
            time.sleep(random.uniform(5.0, 8.0))

    def is_engine_idle(self):
        # Engine is idle if all sessions are IDLE or LISTENING and no conversation active
        modes = self.state.session_mode.values()
        if not modes: return True # No sessions yet
        return all(m in ["IDLE", "LISTENING"] for m in modes) and not getattr(self.state, "is_conversation", False)

    def run(self):
        # Initial wait before starting cycles
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
            else:
                logger.debug("📚 Engine active. Librarian waiting.")
            
            # Wait 30-45 minutes (jitter) before next check
            sleep_time = random.uniform(1800, 2700)
            target = time.time() + sleep_time
            while time.time() < target and self.running:
                time.sleep(10) # Wake briefly to check shutdown flag
