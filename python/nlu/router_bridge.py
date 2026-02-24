import logging
import os
import json
import requests
import re
import concurrent.futures
from ddgs import DDGS
from datetime import datetime

logger = logging.getLogger("LibrarianRouter")

# 🟢 CONFIGURATION
ROUTER_MODEL = "syntheta-brain:latest"
TASK_MODEL = "syntheta-brain:latest" # Maintained for potential future use, but bypassed for web search
OLLAMA_API_URL = "http://localhost:11434/api/generate"

class LibrarianRouter:
    def __init__(self, knowledge_manager):
        self.knowledge = knowledge_manager
        logger.info(f"🧠 Librarian Router Online | LLM Traffic Cop Active ({ROUTER_MODEL}) | Web Interceptor Ready.")

    def _llm_route_query(self, query):
        """
        Uses the Algorithmic Reasoning Engine to strictly categorize the user's intent.
        """
        prompt = f"""
        You are the Omega Hub Routing Core. You must classify the user's input into EXACTLY ONE of five routes. 
        Process the user's request through this strict logical decision tree, in this exact order.

        STEP 1: Check for Physical Actions (reflex_action)
        Does the user want to change their physical environment right now? (e.g., turn on/off, dim, set a timer, stop playing music). 
        -> IF YES, route to "reflex_action". DO NOT proceed to Step 2.

        STEP 2: Check for System/Database Metrics (sql_metrics)
        Is the user asking about the AI's internal software, database status, token usage, logs, or system errors?
        -> IF YES, route to "sql_metrics". DO NOT proceed to Step 3.

        STEP 3: Check for Personal Memory (fetch_memory)
        Is the user asking you to recall a past preference, previous conversation, OR asking about the state of the current chat?
        -> Look for: "me", "my", "I", "we", "last thing", "repeat", "previous", "earlier", "again", "what did you say".
        -> IF YES, route to "fetch_memory". DO NOT proceed to Step 4.

        STEP 4: Check for Live/Real-Time Data (live_web_search)
        Does the user need information that changes constantly or periodically? (e.g., current news, live sports, today's weather, CURRENT WORLD LEADERS, CURRENT CEOS, current events).
        -> IF YES, route to "live_web_search". DO NOT proceed to Step 5.
        -> RULE: Questions like "Who is the President" or "Who is the CEO" change over time. They MUST go to the web.

        STEP 5: Check for Static Facts / Chat (general_knowledge)
        If the query did not trigger Steps 1-4, it belongs here. This includes science, math, history, definitions, and casual greetings.
        -> IF the answer to the question is the EXACT SAME today as it was 50 years ago, it goes here. Do NOT put questions about current politicians here.

        User Query: {query}

        Respond STRICTLY in JSON format with a single key "route".
        Example: {{"route": "live_web_search"}}
        """
        
        payload = {
            "model": ROUTER_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "keep_alive": -1,
            "options": {
                "temperature": 0.0, # Pure logic, zero hallucination
                "num_predict": 50
            }
        }
        
        try:
            # Maintained the 15s timeout to prevent VRAM load crashes
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=15)
            response.raise_for_status()
            raw_text = response.json().get("response", "").strip()
            
            # Robust Regex extraction to bypass formatting anomalies
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                route = parsed.get("route", "general_knowledge")
                # Strict validation against the 5 new lanes
                if route in ["fetch_memory", "live_web_search", "reflex_action", "general_knowledge", "sql_metrics"]:
                    return route
        except Exception as e:
            logger.error(f"⚠️ LLM Routing Failed, defaulting to passthrough: {e}", exc_info=True)
            
        return "general_knowledge" 

    def _quick_web_lookup(self, query):
        """
        High-speed DuckDuckGo interceptor (Snippet-First Pipeline).
        Bypasses the secondary LLM for zero-click latency reduction.
        """
        def _execute_search():
            try:
                with DDGS() as ddgs:
                    # Pull top 3 results to keep context window small and fast
                    results = list(ddgs.text(query, max_results=3))
                    if not results:
                        logger.warning(f"🌐 No results from DDG for '{query}'")
                        return None
                        
                    # Extract ONLY the text snippets directly
                    raw_data = "\n".join([f"Source {i+1}:\nTitle: {r.get('title', 'No Title')}\nSnippet: {r.get('body', 'No snippet')}\n" for i, r in enumerate(results)])
                    
                    current_date = datetime.now().strftime('%B %d, %Y')
                    logger.info(f"🌐 Sniper Web Search Complete. Extracted {len(results)} snippets.")
                    
                    # Return the raw snippets instantly without the Task Model penalty
                    return f"VERIFIED WEB FACTS (As of {current_date}):\n{raw_data}"
                    
            except Exception as e:
                logger.error(f"🌐 Exception in _execute_search: {e}", exc_info=True)
                return None

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_execute_search)
                # Reduced timeout to 5 seconds since we aren't waiting for an LLM anymore
                return future.result(timeout=5.0) 
        except concurrent.futures.TimeoutError:
            logger.error(f"🌐 Web lookup timed out after 5 seconds for query: '{query}'")
            return None
        except Exception as e:
            logger.error(f"🌐 Web Lookup Bypassed: {e}", exc_info=True)
            return None

    def enrich_packet(self, packet):
        """
        The Intercept: Multi-lane routing with live data injection.
        """
        user_input = packet.get('input', '')
        if not user_input:
            packet['route_taken'] = "general_knowledge"
            return packet
            
        # 🟢 HEURISTIC BYPASS: System Constants
        # If the user asks for time/date, we inject it directly and skip the LLM entirely.
        user_input_lower = user_input.lower()
        temporal_keywords = ["time", "date", "today", "day is it", "month"]
        if any(kw in user_input_lower for kw in temporal_keywords) and "who" not in user_input_lower:
            current_time = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p")
            packet['history'] = f"--- SYSTEM CLOCK ---\nToday's Date and Time: {current_time}\n"
            packet['route_taken'] = "system_bypass"
            logger.info("⚡ Librarian: Temporal Heuristic Triggered. Bypassing LLM Router.")
            return packet
        
        # 1. LLM Evaluates the Text Instantly
        route_choice = self._llm_route_query(user_input)
        packet['route_taken'] = route_choice
        
        # LANE 1: THE VAULT (ChromaDB)
        if route_choice == "fetch_memory":
            logger.info("🔎 Librarian: Memory Intent Detected. Searching Vault...")
            
            # Persona Collision Override
            packet['override_topic'] = "fetch_memory" 
            
            context = self.knowledge.get_context(user_input, top_k=3, rerank_k=1)
            if context:
                existing_history = packet.get('history', '')
                packet['history'] = f"--- RETRIEVED PAST MEMORY ---\n{context}\n\n{existing_history}"
                logger.info("✅ Librarian: Memory context injected.")
        
        # LANE 2: THE INTERCEPTOR (Live Web)
        elif route_choice == "live_web_search":
            logger.info(f"🌐 Librarian: Web Intent Detected. Searching for '{user_input}'...")
            live_web_data = self._quick_web_lookup(user_input)
            if live_web_data:
                existing_history = packet.get('history', '')
                packet['history'] = f"--- LIVE WEB SNIPPETS ---\n{live_web_data}\n\n{existing_history}"
                logger.info(f"✅ Librarian: Live web context injected.")
            else:
                logger.warning(f"🌐 No live web data returned for '{user_input}' – LLM will answer from base knowledge.")
            
        # LANE 3, 4, 5: PASSTHROUGH
        else:
            logger.debug(f"⚡ Librarian: Route '{route_choice}'. Passing through.")
        
        return packet