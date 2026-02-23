import logging
import os
import json
from semantic_router import Route
from semantic_router.routers import SemanticRouter
from semantic_router.encoders import HuggingFaceEncoder
from semantic_router.index.local import LocalIndex
from duckduckgo_search import DDGS

logger = logging.getLogger("LibrarianRouter")

class LibrarianRouter:
    def __init__(self, knowledge_manager):
        self.knowledge = knowledge_manager
        
        # 1. Initialize the Encoder (Offline Mode)
        local_model_path = os.path.join(self.knowledge.project_root, "assets/models/all-MiniLM-L6-v2")
        self.encoder = HuggingFaceEncoder(name=local_model_path)
        
        # 2. ROUTE A: The Vault (Memory)
        memory_route = Route(
            name="fetch_memory",
            utterances=[
                "what did we talk about last time?",
                "do you remember what I told you before?",
                "can you recall my preference for that?",
                "what was that thing I mentioned earlier?",
                "pull up the information we discussed previously",
                "remind me of the context from our last chat",
                "retrieve the details from yesterday",
                "did I already tell you about this?",
                "what was the name of that place?",
                "who was that person we were discussing?"
            ]
        )
        
        # 3. ROUTE B: Live Web Search (Immediate Web Intercept)
        web_search_route = Route(
            name="live_web_search",
            utterances=[
                "search the web for",
                "look up the live score for",
                "who won the match last night?",
                "what is the current price of",
                "find a recent article about",
                "what is happening in the world right now regarding",
                "google the details for",
                "check the news for",
                "who is the current",
                "latest updates on"
            ]
        )

        # 4. ROUTE C: Reflex & Bypass (Smart Home / General Chat)
        reflex_route = Route(
            name="reflex_action",
            utterances=[
                "turn on the devices in the room",
                "dim the lights",
                "set a timer",
                "what is the weather right now?",
                "stop what you are doing",
                "hello, how are you today?",
                "tell me a joke",
                "explain how this works"
            ]
        )
        
        # 5. Compile the routes
        routes = [memory_route, web_search_route, reflex_route]

        # 🟢 THE FIX: Forcefully populate the LocalIndex before routing
        self.index = LocalIndex()
        
        # We manually encode and add the utterances for each route to guarantee the index is ready
        logger.info("🧠 Librarian Router: Building and populating vector index...")
        for route in routes:
            # We must use the encoder to convert utterances to vectors
            embeddings = self.encoder(route.utterances)
            self.index.add(
                embeddings=embeddings,
                routes=[route.name] * len(route.utterances),
                utterances=route.utterances
            )
        
        # 6. Compile the Route Layer
        self.router = SemanticRouter(
            encoder=self.encoder, 
            routes=routes,
            index=self.index
        )
        
        logger.info("🧠 Librarian Router Online | Index Baked | Web Interceptor Ready.")

    def _quick_web_lookup(self, query):
        """High-speed DuckDuckGo interceptor (< 1.5s)."""
        try:
            with DDGS() as ddgs:
                # Limit to 3 snippets for maximum speed/context efficiency
                results = [r for r in ddgs.text(query, max_results=3)]
                if results:
                    formatted = "\n".join([f"- {r['body']}" for r in results])
                    return f"LATEST WEB DATA FOR '{query}':\n{formatted}"
        except Exception as e:
            logger.error(f"🌐 Web Lookup Failed: {e}")
        return None

    def enrich_packet(self, packet):
        """
        The Intercept: Multi-lane routing with live data injection.
        """
        user_input = packet.get('input', '')
        
        # 1. Instantly route the text
        route_choice = self.router(user_input).name
        packet['route_taken'] = route_choice
        
        # LANE 1: THE VAULT (ChromaDB)
        if route_choice == "fetch_memory":
            logger.info("🔎 Librarian: Memory Intent Detected. Searching Vault...")
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
                logger.info("✅ Librarian: Live web context injected.")
            
        else:
            logger.debug(f"⚡ Librarian: Route '{route_choice}'. Passing through.")
        
        return packet