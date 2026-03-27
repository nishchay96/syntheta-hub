import sqlite3
import os
import logging
import time
import json
import numpy as np
from datetime import datetime

logger = logging.getLogger("DatabaseManager")


class DatabaseManager:
    def __init__(self):
        current_dir  = os.path.dirname(os.path.abspath(__file__))
        python_root  = os.path.dirname(current_dir)
        self.project_root = os.path.dirname(python_root)

        self.db_dir  = os.path.join(self.project_root, "assets", "database")
        os.makedirs(self.db_dir, exist_ok=True)

        self.db_path = os.path.join(self.db_dir, "syntheta_ledger.db")
        self.init_db()

    # ----------------------------------------------------------
    # CONNECTION
    # ----------------------------------------------------------
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    # ----------------------------------------------------------
    # SCHEMA
    # ----------------------------------------------------------
    def init_db(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("DROP TABLE IF EXISTS conversation_state")

                # Hot Cache (Idle Librarian)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS hot_cache (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bucket TEXT NOT NULL,
                        entity_key TEXT NOT NULL,
                        entity_value TEXT NOT NULL,
                        vector_blob BLOB,
                        last_updated REAL NOT NULL,
                        ttl_seconds INTEGER NOT NULL,
                        UNIQUE(bucket, entity_key)
                    )
                ''')

                # Event Ledger
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS event_ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        date TEXT NOT NULL,
                        resolved_query TEXT NOT NULL,
                        topic_category TEXT,
                        nomic_confidence REAL,
                        extracted_entities TEXT
                    )
                ''')

                # Reflex Telemetry
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS reflex_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_start REAL NOT NULL,
                        timestamp_end REAL,
                        date TEXT NOT NULL,
                        sat_id INTEGER NOT NULL,
                        target_device TEXT NOT NULL,
                        action_executed TEXT NOT NULL,
                        duration_seconds REAL,
                        execution_status TEXT NOT NULL
                    )
                ''')

                # 🟢 FIX: Core Memory is now partitioned by user_id. 
                # The UNIQUE constraint is a composite of (user_id, entity_key)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS core_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        last_updated REAL NOT NULL,
                        date TEXT NOT NULL,
                        user_id TEXT NOT NULL DEFAULT 'Guest',
                        bucket TEXT NOT NULL,
                        entity_key TEXT NOT NULL,
                        entity_value TEXT NOT NULL,
                        confidence_score INTEGER DEFAULT 100,
                        vector_blob BLOB,
                        UNIQUE(user_id, entity_key)
                    )
                ''')

                # Memory Queue
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS memory_queue (
                        task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        date TEXT NOT NULL,
                        interaction_id INTEGER,
                        raw_payload TEXT NOT NULL,
                        status TEXT NOT NULL
                            CHECK(status IN ('THINKING','PENDING','PROCESSING','FAILED'))
                    )
                ''')

                # OpenClaw Job Queue
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS openclaw_jobs (
                        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        status TEXT DEFAULT 'PENDING',
                        task_type TEXT,
                        priority INTEGER DEFAULT 2,
                        description TEXT,
                        parameters JSON,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP
                    )
                ''')

                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS curated_live_cache (
                        cache_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        scope TEXT NOT NULL,
                        user_id TEXT,
                        category TEXT NOT NULL,
                        topic_key TEXT NOT NULL,
                        title TEXT NOT NULL,
                        snippet TEXT,
                        source_name TEXT,
                        source_url TEXT,
                        payload_json TEXT,
                        confidence INTEGER DEFAULT 100,
                        fetched_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        UNIQUE(scope, user_id, category, topic_key, title, source_url)
                    )
                ''')

                # Indices
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_ledger_date    ON event_ledger(date);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_ledger_ts      ON event_ledger(timestamp);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_date ON reflex_telemetry(date);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_core_user_key  ON core_memory(user_id, entity_key);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_status   ON memory_queue(status);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_hot_cache      ON hot_cache(bucket, entity_key);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_curated_scope_topic ON curated_live_cache(scope, user_id, category, topic_key, expires_at);")

                conn.commit()
                logger.info("✅ Database Architecture Upgraded: Multi-User Vector Isolation Active.")

        except Exception as e:
            logger.error(f"❌ Database Init Failed: {e}")

    # ----------------------------------------------------------
    # CRASH RECOVERY
    # ----------------------------------------------------------
    def reset_processing_tasks(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE memory_queue SET status='PENDING' WHERE status='PROCESSING'")
                mem_count = cursor.rowcount
                cursor.execute(
                    "UPDATE openclaw_jobs SET status='PENDING' WHERE status='PROCESSING'")
                job_count = cursor.rowcount
                conn.commit()
                if mem_count > 0 or job_count > 0:
                    logger.info(
                        f"🛠️ Recovered {mem_count} memory tasks "
                        f"and {job_count} OpenClaw jobs.")
                return True
        except Exception as e:
            logger.error(f"❌ Recovery Failed: {e}")
            return False

    # ----------------------------------------------------------
    # EVENT LEDGER
    # ----------------------------------------------------------
    def log_event(self, resolved_query, topic_category="general",
                  nomic_confidence=0.0, extracted_entities=None):
        date_str      = datetime.now().strftime("%Y-%m-%d")
        entities_json = json.dumps(extracted_entities) if extracted_entities else "[]"
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO event_ledger
                        (timestamp, date, resolved_query,
                         topic_category, nomic_confidence, extracted_entities)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (time.time(), date_str, resolved_query,
                      topic_category, nomic_confidence, entities_json))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"❌ Failed to log event: {e}")
            return None

    def get_recent_events(self, limit=5):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT resolved_query FROM event_ledger "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                )
                rows = cursor.fetchall()
                return [row[0] for row in reversed(rows)]
        except Exception as e:
            logger.error(f"❌ get_recent_events failed: {e}")
            return []

    # ----------------------------------------------------------
    # REFLEX TELEMETRY
    # ----------------------------------------------------------
    def log_reflex_start(self, sat_id, target_device, action_executed):
        date_str = datetime.now().strftime("%Y-%m-%d")
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO reflex_telemetry
                        (timestamp_start, date, sat_id,
                         target_device, action_executed, execution_status)
                    VALUES (?, ?, ?, ?, ?, 'SUCCESS')
                ''', (time.time(), date_str, sat_id, target_device, action_executed))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"❌ Failed to log reflex start: {e}")
            return None

    def log_reflex_end(self, telemetry_id):
        end_time = time.time()
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT timestamp_start FROM reflex_telemetry WHERE id=?",
                    (telemetry_id,))
                result = cursor.fetchone()
                if result:
                    duration = end_time - result[0]
                    cursor.execute('''
                        UPDATE reflex_telemetry
                        SET timestamp_end=?, duration_seconds=?
                        WHERE id=?
                    ''', (end_time, duration, telemetry_id))
                    conn.commit()
        except Exception as e:
            logger.error(f"❌ Failed to close reflex action: {e}")

    # ----------------------------------------------------------
    # CORE MEMORY (USER-PARTITIONED)
    # ----------------------------------------------------------
    # 🟢 FIX: Added user_id parameter and updated SQL commands to enforce partition
    def save_core_fact(self, user_id, bucket, entity_key, entity_value,
                       confidence=100, vector=None):
        date_str     = datetime.now().strftime("%Y-%m-%d")
        vector_bytes = (np.array(vector, dtype=np.float32).tobytes()
                        if vector is not None else None)
        
        if isinstance(entity_value, dict):
            entity_value = json.dumps(entity_value, ensure_ascii=False)
            
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO core_memory
                        (last_updated, date, user_id, bucket, entity_key,
                         entity_value, confidence_score, vector_blob)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, entity_key) DO UPDATE SET
                        last_updated     = excluded.last_updated,
                        date             = excluded.date,
                        bucket           = excluded.bucket,
                        entity_value     = excluded.entity_value,
                        confidence_score = excluded.confidence_score,
                        vector_blob      = excluded.vector_blob
                ''', (time.time(), date_str, user_id, bucket, entity_key.lower(),
                      entity_value, confidence, vector_bytes))
                conn.commit()
                logger.info(f"🧠 UPSERT: [{user_id} | {entity_key}] → Node Captured")
        except Exception as e:
            logger.error(f"❌ Failed to save core fact: {e}")

    def delete_core_fact(self, user_id, key):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM core_memory WHERE user_id=? AND entity_key=?",
                    (user_id, key.lower()))
                if cursor.rowcount > 0:
                    logger.info(f"🗑️ Erased fact: '{key}' for user '{user_id}'")
                conn.commit()
        except Exception as e:
            logger.error(f"❌ Failed to delete core fact: {e}")

    def get_all_core_facts(self, user_id):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT entity_key, entity_value, bucket "
                    "FROM core_memory WHERE user_id=? "
                    "ORDER BY last_updated DESC",
                    (user_id,)
                )
                return {
                    row[0]: {"value": row[1], "bucket": row[2]}
                    for row in cursor.fetchall()
                }
        except Exception as e:
            logger.error(f"❌ get_all_core_facts failed: {e}")
            return {}

    def get_all_user_buckets(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT bucket FROM core_memory WHERE user_id != 'Guest'")
                return [row[0] for row in cursor.fetchall() if row[0]]
        except Exception as e:
            logger.error(f"❌ get_all_user_buckets failed: {e}")
            return []

    def get_relevant_memories(self, user_id, query_vector, top_k=3):
        """
        Cosine-similarity search over core_memory vector_blob column, strictly filtered by user_id.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT entity_key, entity_value, vector_blob "
                    "FROM core_memory WHERE vector_blob IS NOT NULL AND user_id=?",
                    (user_id,)
                )
                rows = cursor.fetchall()

            if not rows:
                return []

            q_vec  = np.array(query_vector, dtype=np.float32)
            scored = []
            for key, value, blob in rows:
                mem_vec    = np.frombuffer(blob, dtype=np.float32)
                denom      = np.linalg.norm(q_vec) * np.linalg.norm(mem_vec)
                similarity = float(np.dot(q_vec, mem_vec) / denom) if denom > 0 else 0.0
                scored.append((similarity, key, value))

            scored.sort(reverse=True, key=lambda x: x[0])
            
            results = []
            for sim, key, value_str in scored[:top_k]:
                if sim > 0.50:
                    try:
                        val_dict = json.loads(value_str)
                        formatted_val = json.dumps(val_dict, indent=2)
                        results.append(f"[{key.upper()}]\n{formatted_val}")
                    except json.JSONDecodeError:
                        results.append(f"[{key.upper()}]\n{value_str}")
                        
            return results

        except Exception as e:
            logger.error(f"❌ Semantic Retrieval Failed: {e}")
            return []
        
    # ----------------------------------------------------------
    # ASYNC QUEUE (NIGHTWATCHMAN)
    # ----------------------------------------------------------
    def create_memory_task(self, payload_dict, interaction_id=None):
        date_str = datetime.now().strftime("%Y-%m-%d")
        try:
            payload_json = json.dumps(payload_dict)
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO memory_queue
                        (timestamp, date, interaction_id, raw_payload, status)
                    VALUES (?, ?, ?, ?, 'PENDING')
                ''', (time.time(), date_str, interaction_id, payload_json))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"❌ Task creation failed: {e}")
            return None

    # ----------------------------------------------------------
    # IDLE LIBRARIAN (HOT CACHE)
    # ----------------------------------------------------------
    def save_hot_cache(self, bucket, entity_key, entity_value, vector=None, ttl_seconds=3600):
        vector_bytes = (np.array(vector, dtype=np.float32).tobytes()
                        if vector is not None else None)
        
        if isinstance(entity_value, dict):
            entity_value = json.dumps(entity_value, ensure_ascii=False)
            
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO hot_cache
                        (bucket, entity_key, entity_value, vector_blob, last_updated, ttl_seconds)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(bucket, entity_key) DO UPDATE SET
                        entity_value = excluded.entity_value,
                        vector_blob  = excluded.vector_blob,
                        last_updated = excluded.last_updated,
                        ttl_seconds  = excluded.ttl_seconds
                ''', (bucket, entity_key.lower(), entity_value, vector_bytes, time.time(), ttl_seconds))
                conn.commit()
        except Exception as e:
            logger.error(f"❌ Failed to save hot cache: {e}")

    def clean_expired_cache(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                now = time.time()
                cursor.execute("DELETE FROM hot_cache WHERE (? - last_updated) > ttl_seconds", (now,))
                deleted = cursor.rowcount
                conn.commit()
                if deleted > 0:
                    logger.info(f"🧹 Cleared {deleted} expired entries from Hot Cache.")
        except Exception as e:
            logger.error(f"❌ Failed to clean hot cache: {e}")

    def get_hot_cache_match(self, query_vector, threshold=0.85):
        self.clean_expired_cache()
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT entity_key, entity_value, vector_blob FROM hot_cache WHERE vector_blob IS NOT NULL")
                rows = cursor.fetchall()
            
            if not rows:
                return None
                
            q_vec = np.array(query_vector, dtype=np.float32)
            scored = []
            for key, value, blob in rows:
                mem_vec = np.frombuffer(blob, dtype=np.float32)
                denom = np.linalg.norm(q_vec) * np.linalg.norm(mem_vec)
                similarity = float(np.dot(q_vec, mem_vec) / denom) if denom > 0 else 0.0
                scored.append((similarity, key, value))
                
            scored.sort(reverse=True, key=lambda x: x[0])
            best_sim, best_key, best_val = scored[0]
            
            if best_sim >= threshold:
                logger.info(f"🔥 Hot Cache HIT: [{best_key}] (score: {best_sim:.3f})")
                return best_val
            return None
        except Exception as e:
            logger.error(f"❌ Hot Cache search failed: {e}")
            return None

    # ----------------------------------------------------------
    # CURATED LIVE CACHE
    # ----------------------------------------------------------
    def replace_curated_topic(self, scope, category, topic_key, items, user_id=None, ttl_seconds=600):
        now = time.time()
        expires_at = now + ttl_seconds
        scope_key = str(scope or "global")
        user_key = str(user_id).lower() if user_id else None
        category_key = str(category)
        topic_key = str(topic_key)
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM curated_live_cache WHERE scope=? AND IFNULL(user_id,'')=IFNULL(?, '') "
                    "AND category=? AND topic_key=?",
                    (scope_key, user_key, category_key, topic_key),
                )
                for item in items:
                    cursor.execute(
                        '''
                        INSERT INTO curated_live_cache
                            (scope, user_id, category, topic_key, title, snippet, source_name,
                             source_url, payload_json, confidence, fetched_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            scope_key,
                            user_key,
                            category_key,
                            topic_key,
                            item.get("title", ""),
                            item.get("snippet"),
                            item.get("source_name"),
                            item.get("source_url"),
                            json.dumps(item.get("payload_json")) if item.get("payload_json") is not None else None,
                            int(item.get("confidence", 100)),
                            now,
                            expires_at,
                        ),
                    )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ replace_curated_topic failed: {e}")
            return False

    def get_curated_topic(self, scope, category, topic_key, user_id=None, limit=10):
        scope_key = str(scope or "global")
        user_key = str(user_id).lower() if user_id else None
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT title, snippet, source_name, source_url, payload_json, confidence, fetched_at, expires_at
                    FROM curated_live_cache
                    WHERE scope=? AND IFNULL(user_id,'')=IFNULL(?, '')
                      AND category=? AND topic_key=? AND expires_at>?
                    ORDER BY confidence DESC, fetched_at DESC, cache_id ASC
                    LIMIT ?
                    ''',
                    (scope_key, user_key, category, topic_key, time.time(), limit),
                )
                rows = cursor.fetchall()
                items = []
                for row in rows:
                    payload = row[4]
                    items.append({
                        "title": row[0],
                        "snippet": row[1],
                        "source_name": row[2],
                        "source_url": row[3],
                        "payload_json": json.loads(payload) if payload else None,
                        "confidence": row[5],
                        "fetched_at": row[6],
                        "expires_at": row[7],
                    })
                return items
        except Exception as e:
            logger.error(f"❌ get_curated_topic failed: {e}")
            return []

    # ----------------------------------------------------------
    # OPENCLAW JOBS
    # ----------------------------------------------------------
    def get_pending_openclaw_jobs(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT job_id, description, parameters FROM openclaw_jobs "
                    "WHERE status='PENDING' ORDER BY priority ASC, created_at ASC"
                )
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"❌ Failed to fetch pending OpenClaw jobs: {e}")
            return []

    def update_openclaw_job_status(self, job_id, status):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE openclaw_jobs SET status=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE job_id=?", (status, job_id)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Failed to update OpenClaw job status: {e}")
            return False

    def insert_openclaw_job(self, task_type, description, parameters=None, priority=5):
        """Insert a new research job for the OpenClaw worker to process."""
        import json
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO openclaw_jobs (status, task_type, priority, description, parameters) "
                    "VALUES ('PENDING', ?, ?, ?, ?)",
                    (task_type, priority, description,
                     json.dumps(parameters) if parameters else None)
                )
                conn.commit()
                job_id = cursor.lastrowid
                logger.info(f"📋 Queued OpenClaw {task_type} job #{job_id}: {description[:60]}")
                return job_id
        except Exception as e:
            logger.error(f"❌ Failed to insert OpenClaw job: {e}")
            return None
