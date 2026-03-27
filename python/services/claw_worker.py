import json
import logging
import os
import re
import time

import requests

from core.database_manager import DatabaseManager
from nlu.api_scout import APIScout
from services.config import GLOBAL_WEATHER_CITY, KNOWLEDGE_VAULT_PATH

logger = logging.getLogger("OpenClawWorker")

GLOBAL_REFRESH_TTL_SEC = 600
USER_REFRESH_TTL_SEC = 900
POLL_INTERVAL_SEC = 180


class OpenClawWorker:
    """
    Background curation worker.

    OpenClaw proactively refreshes a curated live-cache for:
    - global feeds every user is likely to ask for
    - current-profile interests derived from durable profile buckets

    It is not a second chatbot and it does not replace live retrieval.
    """

    def __init__(self):
        self.db = DatabaseManager()
        self.scout = APIScout()
        self.identity_state_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../assets/system/identity_state.json")
        )
        logger.info("🤖 OpenClaw Worker initialized | Mode: curation")

    def run_forever(self):
        logger.info("🤖 OpenClaw Worker active. Curating live cache...")
        while True:
            try:
                self._refresh_curated_cache()
                self._process_pending_tasks()
            except Exception as e:
                logger.error(f"❌ OpenClaw Worker loop error: {e}")
            time.sleep(POLL_INTERVAL_SEC)

    def _refresh_curated_cache(self):
        self._refresh_global_news()
        self._refresh_global_weather()

        active_profiles = self._get_active_profiles()
        all_profiles = self._get_all_profiles()

        ordered_profiles = []
        seen = set()
        for profile in active_profiles + all_profiles:
            if profile and profile not in seen and profile != "guest":
                ordered_profiles.append(profile)
                seen.add(profile)

        for index, user_id in enumerate(ordered_profiles):
            self._refresh_user_interest_news(user_id, high_priority=(index == 0))

    def _process_pending_tasks(self):
        pending = self.db.get_pending_openclaw_jobs()
        if not pending:
            return

        for job_id, description, params_json in pending:
            logger.info(f"🔬 Processing OpenClaw job #{job_id}: {description}")
            self.db.update_openclaw_job_status(job_id, "PROCESSING")
            try:
                params = json.loads(params_json) if params_json else {}
                task_type = (params.get("task_type") or "").strip().lower()
                if task_type == "refresh_interest_news":
                    user_id = (params.get("user_id") or "").strip().lower()
                    topic = (params.get("topic") or "").strip()
                    if user_id and topic:
                        self._refresh_interest_topic(user_id, topic, USER_REFRESH_TTL_SEC)
                self.db.update_openclaw_job_status(job_id, "COMPLETED")
            except Exception as e:
                self.db.update_openclaw_job_status(job_id, "FAILED")
                logger.error(f"❌ OpenClaw job #{job_id} failed: {e}")

    def _refresh_global_news(self):
        cached = self.db.get_curated_topic("global", "News", "top_today", limit=10)
        if cached:
            return

        try:
            res = requests.get(
                "https://ok.surf/api/v1/cors/news-feed",
                timeout=10.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            logger.warning(f"⚠️ OpenClaw global news refresh failed: {e}")
            return

        preferred_sections = ["US", "World", "Business", "Technology", "Science", "Sports"]
        items = []
        seen_titles = set()
        for section in preferred_sections:
            for row in data.get(section, []):
                title = (row.get("title") or "").strip()
                source = (row.get("source") or section).strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                items.append({
                    "title": title,
                    "snippet": row.get("description") or "",
                    "source_name": source,
                    "source_url": row.get("link") or "",
                    "payload_json": row,
                    "confidence": 95,
                })
                if len(items) >= 10:
                    break
            if len(items) >= 10:
                break

        if items:
            self.db.replace_curated_topic(
                scope="global",
                user_id=None,
                category="News",
                topic_key="top_today",
                items=items,
                ttl_seconds=GLOBAL_REFRESH_TTL_SEC,
            )
            logger.info(f"📰 OpenClaw refreshed global top news ({len(items)} items)")

    def _refresh_global_weather(self):
        topic_key = self._topic_key(f"{GLOBAL_WEATHER_CITY}_current")
        cached = self.db.get_curated_topic("global", "Weather", topic_key, limit=1)
        if cached:
            return

        try:
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": GLOBAL_WEATHER_CITY, "count": 1, "language": "en", "format": "json"},
                timeout=8.0,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            geo.raise_for_status()
            results = geo.json().get("results", [])
            if not results:
                return

            place = results[0]
            forecast = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,wind_speed_10m",
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
                return
        except Exception as e:
            logger.warning(f"⚠️ OpenClaw global weather refresh failed: {e}")
            return

        location_label = f"{place['name']}, {place.get('admin1', place.get('country', ''))}".strip(", ")
        title_bits = [f"Current weather in {location_label}"]
        if temp is not None:
            title_bits.append(f"{round(float(temp))}°C")
        if wind is not None:
            title_bits.append(f"{round(float(wind))} km/h wind")
        title = " | ".join(title_bits)

        self.db.replace_curated_topic(
            scope="global",
            user_id=None,
            category="Weather",
            topic_key=topic_key,
            items=[{
                "title": title,
                "snippet": "",
                "source_name": "Open-Meteo",
                "source_url": "",
                "payload_json": {
                    "city": place["name"],
                    "admin1": place.get("admin1"),
                    "country": place.get("country"),
                    "temperature_c": temp,
                    "wind_kmh": wind,
                },
                "confidence": 95,
            }],
            ttl_seconds=GLOBAL_REFRESH_TTL_SEC,
        )
        logger.info(f"🌦️ OpenClaw refreshed global weather for {location_label}")

    def _refresh_user_interest_news(self, user_id: str, high_priority: bool = False):
        interests = self._get_user_interest_topics(user_id)
        if not interests:
            return
        max_topics = 3 if high_priority else 2
        for topic in interests[:max_topics]:
            topic_key = self._topic_key(topic)
            cached = self.db.get_curated_topic("user", "News", topic_key, user_id=user_id, limit=3)
            if cached:
                continue
            self._refresh_interest_topic(user_id, topic, USER_REFRESH_TTL_SEC)

    def _refresh_interest_topic(self, user_id: str, topic: str, ttl_seconds: int):
        try:
            items = self.scout._fetch_google_news_rss(topic, limit=4)
        except Exception as e:
            logger.warning(f"⚠️ OpenClaw interest refresh failed for {user_id}:{topic}: {e}")
            return

        curated = []
        for title, url in items[:4]:
            if not title:
                continue
            curated.append({
                "title": title,
                "snippet": "",
                "source_name": "Google News RSS",
                "source_url": url or "",
                "payload_json": {"topic": topic},
                "confidence": 90,
            })

        if curated:
            self.db.replace_curated_topic(
                scope="user",
                user_id=user_id,
                category="News",
                topic_key=self._topic_key(topic),
                items=curated,
                ttl_seconds=ttl_seconds,
            )
            logger.info(f"📰 OpenClaw refreshed {user_id} interest news for '{topic}' ({len(curated)} items)")

    def _get_active_profiles(self) -> list[str]:
        if not os.path.exists(self.identity_state_path):
            return []
        try:
            with open(self.identity_state_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            current_date = time.strftime("%Y-%m-%d")
            profiles = []
            for state in raw.values():
                if not isinstance(state, dict):
                    continue
                if state.get("loaded_date") != current_date:
                    continue
                active_user = str(state.get("active_user") or "").strip().lower()
                if active_user and active_user != "guest":
                    profiles.append(active_user)
            return profiles
        except Exception as e:
            logger.warning(f"⚠️ Failed to read active profiles for OpenClaw: {e}")
            return []

    def _get_all_profiles(self) -> list[str]:
        try:
            entries = []
            for entry in os.listdir(KNOWLEDGE_VAULT_PATH):
                full = os.path.join(KNOWLEDGE_VAULT_PATH, entry)
                if os.path.isdir(full) and entry not in {"guest"} and not entry.startswith("sat_"):
                    entries.append(entry.lower())
            return sorted(entries)
        except Exception as e:
            logger.warning(f"⚠️ Failed to list profiles for OpenClaw: {e}")
            return []

    def _get_user_interest_topics(self, user_id: str) -> list[str]:
        opinions_path = os.path.join(KNOWLEDGE_VAULT_PATH, user_id, "Bucket_Opinions.json")
        if not os.path.exists(opinions_path):
            return []
        try:
            with open(opinions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []

        topics = []
        for node_name, attrs in (data.get("nodes") or {}).items():
            name = str(node_name).strip()
            low = name.lower()
            if not name or low in {"none", "interest in something"}:
                continue
            preference = str((attrs or {}).get("Preference", "")).lower()
            if any(word in preference for word in ["liked", "love", "interested", "concerned", "follow"]):
                topics.append(name)
            elif len(name.split()) <= 4 and low not in {"something", "none"}:
                topics.append(name)

        deduped = []
        seen = set()
        for topic in topics:
            key = self._topic_key(topic)
            if key not in seen:
                deduped.append(topic)
                seen.add(key)
        return deduped

    def _topic_key(self, topic: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")
