import time
import json
import logging
import threading
import sqlite3

from core.database_manager import DatabaseManager
from core.knowledge_manager import KnowledgeManager
from nlu.llm_bridge import OllamaBridge

logger = logging.getLogger("NightWatchman")

class MemoryWorker:
    def __init__(self, state_manager):
        self.state = state_manager
        self.db_manager = DatabaseManager()
        self.knowledge = KnowledgeManager()
        self.llm = OllamaBridge()
        self.running = True

    def start(self):
        logger.info("🦉 Night Watchman online. Waiting for silence to process memories...")
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _is_system_idle(self):
        """Ensures the worker ONLY runs when Syntheta is completely asleep."""
        if not self.state.session_mode: return True
        return all(mode == "IDLE" for mode in self.state.session_mode.values())

    def _worker_loop(self):
        while self.running:
            time.sleep(5)  # Check every 5 seconds
            
            if not self._is_system_idle():
                continue

            try:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    
                    # 1. Grab the oldest PENDING task
                    cursor.execute("SELECT task_id, raw_payload FROM memory_queue WHERE status = 'PENDING' ORDER BY timestamp ASC LIMIT 1")
                    row = cursor.fetchone()
                    
                    if not row:
                        continue # Nothing to process
                        
                    task_id, raw_payload = row
                    
                    # 2. Lock the row (UPDATE to PROCESSING)
                    cursor.execute("UPDATE memory_queue SET status = 'PROCESSING' WHERE task_id = ?", (task_id,))
                    conn.commit()
                    
            except Exception as e:
                logger.error(f"⚠️ Database Error: {e}")
                continue

            # 3. Process the Task
            logger.info(f"🦉 System is idle. Processing memory task {task_id}...")
            success = self._process_memory(raw_payload)

            # 4. The Atomic Drop (or Re-queue on failure)
            try:
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    if success:
                        cursor.execute("DELETE FROM memory_queue WHERE task_id = ?", (task_id,))
                        logger.info(f"🧹 Task {task_id} fully consolidated and cleared from queue.")
                    else:
                        cursor.execute("UPDATE memory_queue SET status = 'FAILED' WHERE task_id = ?", (task_id,))
                    conn.commit()
            except Exception as e:
                logger.error(f"⚠️ Failed to execute Atomic Drop for task {task_id}: {e}")

    def _process_memory(self, raw_payload):
        try:
            data = json.loads(raw_payload)
            user_query = data.get("user_query", "")
            llm_response = data.get("llm_response", "")
            
            # The prompt to force Ollama to extract factual truths
            prompt = f"""
            Analyze the following interaction between a human user and an AI named Syntheta.
            Extract any permanent factual truths or preferences about the user. If there are none, output an empty list.
            
            User: {user_query}
            Syntheta: {llm_response}
            
            Respond STRICTLY in this JSON format:
            {{
                "episodic_summary": "A 1-sentence summary of what happened.",
                "factual_truths": ["Fact 1", "Fact 2"]
            }}
            """
            
            # Generate via LLM (Assuming OllamaBridge returns the raw text or a dict)
            # We wrap it in a pseudo-golden packet to satisfy the bridge
            packet = {"role": "You are a data extraction AI.", "history": "", "ctx": "memory_consolidation", "input": prompt, "emotion": "neutral"}
            
            response_data = self.llm.generate(packet)
            
            # Parse the LLM output
            if isinstance(response_data, dict):
                content = response_data.get("response", "{}")
            else:
                content = response_data
                
            # Clean Markdown formatting if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
                
            memory_json = json.loads(content)
            
            summary = memory_json.get("episodic_summary", "")
            truths = memory_json.get("factual_truths", [])
            
            if not summary and not truths:
                return True # Nothing worth saving, but successfully processed!
                
            # Store in ChromaDB
            full_memory_text = f"Summary: {summary}\nLearned Facts: {', '.join(truths)}"
            metadata = {"source": "conversation_log", "topic": data.get("topic", "general")}
            
            return self.knowledge.add_memory(full_memory_text, metadata)
            
        except json.JSONDecodeError:
            logger.warning("⚠️ LLM failed to output valid JSON for memory processing.")
            return False
        except Exception as e:
            logger.error(f"❌ Memory Processing Crash: {e}")
            return False