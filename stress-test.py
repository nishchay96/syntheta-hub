import os
import sys
import time
from unittest.mock import MagicMock

# Setup Paths
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON_DIR = os.path.join(PROJECT_ROOT, 'python')
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

# Mock hardware to prevent ESP triggers
sys.modules['core.pi_manager'] = MagicMock()

from services.engine import SynthetaEngine
from services.state_manager import EngineState

def run_web_test():
    print("===================================================")
    print("🌐 SYNTHETA WEB SEARCH PIPELINE TEST (SEARXNG)")
    print("===================================================\n")
    
    state = EngineState()
    pi_mock = MagicMock()
    pi_mock.process_query.return_value = None 
    
    # Initialize the Engine
    engine = SynthetaEngine(state_manager=state, pi_manager=pi_mock)
    
    if not getattr(engine, 'librarian', None):
        print("⚠️ WARNING: LibrarianRouter failed to load. The search will likely fail.")
    
    # 🟢 INTERCEPTOR: Catch the packet right before it hits the LLM
    original_generate = engine.llm.generate
    def mock_generate(packet):
        print("\n" + "▼"*50)
        print("📦 THE GOLDEN PACKET (What the LLM sees):")
        print("▼"*50)
        
        route = packet.get('route_taken', 'unknown')
        print(f"ROUTE TAKEN: {route}")
        print("-" * 50)
        
        history = packet.get('history', '')
        if history:
            print("[HISTORY INJECTION]:")
            print(history)
            
            # Diagnostic flag for SearxNG success
            if "--- LIVE WEB ---" in history:
                print("\n✅ SUCCESS: SearxNG Live Web Data successfully injected!")
            elif route == "general_web_search":
                print("\n❌ FAILED: Route was web search, but SearxNG returned no data.")
        else:
            print("[HISTORY INJECTION]: (Empty)")
            
        print(f"\n[USER INPUT]:\n{packet.get('input', '')}")
        print("▲"*50 + "\n")
        
        return original_generate(packet)
        
    engine.llm.generate = mock_generate
    engine._speak = lambda sid, text, **kwargs: print(f"💬 SYNTHETA SAYS: {text}\n")
    
    sat_id = 1
    engine.state.session_mode[sat_id] = "LISTENING"
    engine.state.is_conversation = True
    
    # 🧪 The Test Queries
    test_queries = [
        "What is the current stock price of NVIDIA?",
        "What is the weather like in Tokyo right now?",
        "Who won the most recent Formula 1 Grand Prix?"
    ]
    
    print("\n🚀 Commencing SearxNG Web Search Injection Tests...")
    
    for query in test_queries:
        print(f"\n" + "="*60)
        print(f"🗣️ YOU: {query}")
        print("="*60)
        
        try:
            telemetry = {"start_time": time.perf_counter()}
            engine._handle_normal_command(sat_id, query, telemetry)
        except Exception as e:
            print(f"❌ Pipeline Error on query '{query}': {e}")
            
        time.sleep(2) # Brief pause between tests

if __name__ == "__main__":
    run_web_test()