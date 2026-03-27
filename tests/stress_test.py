import os
import sys
import time
import json
import threading
from unittest.mock import MagicMock

# Setup Paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.join(PROJECT_ROOT, 'python')
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from services.engine import SynthetaEngine
from services.state_manager import EngineState
from core.pi_manager import PiManager

class SynthetaStressTester:
    def __init__(self):
        print("🚀 Initializing Stress Test Engine...")
        self.state = EngineState()
        # 🟢 FIX: PiManager requires engine_state
        self.pi = PiManager(self.state) 
        
        # Initialize Engine
        self.engine = SynthetaEngine(state_manager=self.state, pi_manager=self.pi)
        
        # Performance Tracking
        self.results = []
        self.current_test = {}
        
        # Monkeypatch _speak to capture output and timing
        self.engine._speak = self._captured_speak
        
        # Monkeypatch LibrarianRouter to capture routing decision
        self.original_enrich = self.engine.librarian.enrich_packet
        self.engine.librarian.enrich_packet = self._captured_enrich

    def _captured_speak(self, sid, text, **kwargs):
        self.current_test['response'] = text
        self.current_test['end_time'] = time.perf_counter()
        self.current_test['latency_ms'] = (self.current_test['end_time'] - self.current_test['start_time']) * 1000
        print(f"   [DONE] Latency: {self.current_test['latency_ms']:.2f}ms")

    def _captured_enrich(self, packet):
        res = self.original_enrich(packet)
        self.current_test['route'] = res.get('route_taken', 'unknown')
        self.current_test['web_matched'] = True if res.get('web_data') else False
        return res

    def run_query(self, query):
        print(f"\n▶️ TEST: '{query}'")
        self.current_test = {
            'query': query,
            'start_time': time.perf_counter(),
            'response': None,
            'route': None,
            'web_matched': False
        }
        
        sat_id = 1
        self.engine.state.session_mode[sat_id] = "LISTENING"
        self.engine.handle_input(sat_id, query, {"start_time": time.perf_counter()})
        
        # Wait for async processing to finish
        timeout = 60
        start_wait = time.time()
        while self.current_test['response'] is None and (time.time() - start_wait) < timeout:
            time.sleep(0.1)
            
        if self.current_test['response'] is None:
            print("   [TIMEOUT] No response within 60s")
            self.current_test['latency_ms'] = 60000
            
        self.results.append(self.current_test)

def main():
    tester = SynthetaStressTester()
    
    test_matrix = [
        # 🟢 Reflex (Fast Path)
        ("hi", "reflex_match"),
        ("who are you", "reflex_match"),
        
        # 🟢 Identity/CASE C (Rejection)
        ("weather today", "general_web_search"),
        
        # 🟢 Web (APIScout)
        ("top news for today", "general_web_search"),
        ("bitcoin price", "general_web_search"),
        
        # 🟢 Fallback (Search)
        ("who won the game yesterday", "general_web_search"),
        
        # 🟢 Memory (Brain)
        ("what was my last query?", "general_no_web")
    ]
    
    print("\n" + "="*50)
    print("🏁 STARTING STRESS TEST")
    print("="*50)
    
    for query, expected_route in test_matrix:
        tester.run_query(query)
        
    print("\n" + "="*50)
    print("📊 FINAL RESULTS")
    print("="*50)
    
    summary = []
    for (query, expected_route), r in zip(test_matrix, tester.results):
        status = "PASS"
        actual = r['route'] or "Reflex"
        
        if expected_route == "reflex_match":
            if actual != "Reflex": status = "FAIL (Expected Reflex)"
        elif actual != expected_route:
            status = f"FAIL (Expected {expected_route})"
        
        summary.append({
            "Query": query,
            "Actual Route": actual,
            "Latency": f"{r.get('latency_ms', 0):.0f}ms",
            "Status": status
        })
    
    import pandas as pd
    df = pd.DataFrame(summary)
    print(df.to_markdown())

if __name__ == "__main__":
    main()