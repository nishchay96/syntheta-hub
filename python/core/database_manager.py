import sqlite3
import os
import logging
import time
import json

logger = logging.getLogger("DatabaseManager")

class DatabaseManager:
    def __init__(self):
        # Dynamically resolve path to syntheta-hub/assets/database
        current_dir = os.path.dirname(os.path.abspath(__file__))
        python_root = os.path.dirname(current_dir)
        self.project_root = os.path.dirname(python_root)
        
        self.db_dir = os.path.join(self.project_root, "assets", "database")
        os.makedirs(self.db_dir, exist_ok=True)
        
        self.db_path = os.path.join(self.db_dir, "syntheta_ledger.db")
        self.init_db()

    def get_connection(self):
        # check_same_thread=False allows our background workers and main engine to share the DB
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def init_db(self):
        """Creates the memory queue table with strict ENUM constraints and the reflex training ledger."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # 🟢 UPGRADE: Added 'THINKING' state for Write-Ahead Logging
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS memory_queue (
                        task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        raw_payload TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('THINKING', 'PENDING', 'PROCESSING', 'FAILED'))
                    )
                ''')
                # 🟢 UPGRADE: Dedicated 30-Day Reflex Telemetry Storage
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS reflex_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        sat_id INTEGER NOT NULL,
                        intent TEXT NOT NULL,
                        raw_input TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                ''')
                conn.commit()
                # Suppress log on every init to avoid spam, just ensure it exists
        except Exception as e:
            logger.error(f"❌ Database Init Failed: {e}")

    def reset_processing_tasks(self):
        """Boot-Up Recovery: Resets orphaned PROCESSING or THINKING tasks to PENDING if power was lost."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE memory_queue 
                    SET status = 'PENDING' 
                    WHERE status IN ('PROCESSING', 'THINKING')
                ''')
                recovered = cursor.rowcount
                conn.commit()
                if recovered > 0:
                    logger.info(f"🔄 Boot Recovery: Reset {recovered} orphaned memory tasks to PENDING.")
        except Exception as e:
            logger.error(f"❌ Boot Recovery Failed: {e}")

    def create_memory_task(self, payload_dict):
        """Write-Ahead Logging: Secures the input before the SLM begins processing."""
        try:
            payload_json = json.dumps(payload_dict)
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO memory_queue (timestamp, raw_payload, status)
                    VALUES (?, ?, 'THINKING')
                ''', (time.time(), payload_json))
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"❌ Failed to create memory task: {e}")
            return None

    def update_memory_task(self, task_id, payload_dict):
        """Finalizes the memory task with the SLM response and marks it ready for the NightWatchman."""
        if not task_id: 
            return
        try:
            payload_json = json.dumps(payload_dict)
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE memory_queue 
                    SET raw_payload = ?, status = 'PENDING' 
                    WHERE task_id = ?
                ''', (payload_json, task_id))
                conn.commit()
        except Exception as e:
            logger.error(f"❌ Failed to update memory task {task_id}: {e}")

    def insert_reflex_telemetry(self, sat_id, intent, raw_input, payload_dict):
        """Immutable logging for training datasets."""
        try:
            payload_json = json.dumps(payload_dict)
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO reflex_telemetry (timestamp, sat_id, intent, raw_input, payload)
                    VALUES (?, ?, ?, ?, ?)
                ''', (time.time(), sat_id, intent, raw_input, payload_json))
                conn.commit()
        except Exception as e:
            logger.error(f"❌ Failed to log reflex telemetry: {e}")