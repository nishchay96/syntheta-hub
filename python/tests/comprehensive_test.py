import time
import logging
import sys
import os
import json

# 🔧 SETUP PATHS
sys.path.append(os.path.join(os.path.dirname(__file__), '../'))

# 🔇 SILENCE LOGS FOR CLEAN OUTPUT
logging.basicConfig(level=logging.CRITICAL)
logger = logging.getLogger("SynthetaEngine")
logger.setLevel(logging.CRITICAL)

from services.engine import SynthetaEngine

# ==========================================
# 🧪 TEST HARNESS
# ==========================================
class TestSuite:
    def __init__(self):
        print("⚙️  BOOTING SYNTHETA CORE (This takes ~5s)...")
        self.engine = SynthetaEngine()
        
        # MOCK OUTPUTS to avoid Hardware/TTS errors during logic testing
        self.engine.tts = None 
        self.engine.emitter.emit = self._mock_emit
        self.last_event = None
        
        print("✅ SYSTEM ONLINE. BEGINNING AUDIT.\n")
        self.results = []

    def _mock_emit(self, event_type, sat_id, data):
        self.last_event = {"type": event_type, "data": data}

    def run_scenario(self, name, input_text, mode="clean", context=None, checks=[], expect_none=False):
        print(f"🔹 SCENARIO: {name}")
        print(f"   Input: '{input_text}' | Mode: {mode}")
        if context: print(f"   Context: {json.dumps(context)}")

        t0 = time.time()
        
        # 1. INJECT INTO BRAIN
        plan = self.engine.pi.process_query(input_text, mode=mode, interrupted_context=context)
        latency = time.time() - t0
        
        # 2. VALIDATE CHECKS
        passed = True
        failure_reason = ""

        # Check if we explicitly expect NO plan (the Silence Guard logic)
        if expect_none:
            if plan is not None:
                passed = False
                failure_reason = "Expected None (Ignore), but got a Plan"
        
        # Standard Plan validation
        elif not plan:
            passed = False
            failure_reason = "No Plan Returned"
        else:
            for key, expected_val in checks:
                actual_val = plan.get(key)
                
                # SPECIAL MATCHERS
                if expected_val == "*PRESENT*":
                    if not actual_val:
                        passed = False
                        failure_reason = f"Missing key '{key}'"
                elif expected_val == "*MISSING*":
                    if actual_val:
                        passed = False
                        failure_reason = f"Unexpected key '{key}'"
                elif isinstance(expected_val, list):
                     if actual_val not in expected_val:
                        passed = False
                        failure_reason = f"'{key}' was '{actual_val}', expected one of {expected_val}"
                elif isinstance(expected_val, str) and expected_val not in str(actual_val) and expected_val != actual_val:
                    passed = False
                    failure_reason = f"'{key}' was '{actual_val}', expected '{expected_val}'"
                elif not isinstance(expected_val, (list, str)) and actual_val != expected_val:
                    passed = False
                    failure_reason = f"'{key}' was '{actual_val}', expected '{expected_val}'"
                
                if not passed: break

        # 3. LOG RESULT
        status = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            print(f"   ⚠️  FAILURE: {failure_reason}")
            if plan: print(f"   ⚠️  ACTUAL PLAN: {json.dumps(plan, indent=2)}")
        
        self.results.append({
            "name": name,
            "passed": passed,
            "latency": latency,
            "notes": failure_reason
        })
        print(f"   {status} ({latency:.3f}s)\n")

    def print_report(self):
        print("="*70)
        print(f"{'STATUS':<8} | {'SCENARIO':<35} | {'LATENCY':<8} | {'NOTES'}")
        print("-" * 70)
        score = 0
        for r in self.results:
            status = "PASS" if r['passed'] else "FAIL"
            if r['passed']: score += 1
            print(f"{status:<8} | {r['name']:<35} | {r['latency']:.3f}s | {r['notes']}")
        print("="*70)
        print(f"FINAL SCORE: {score}/{len(self.results)}")

# ==========================================
# 🚀 EXECUTION
# ==========================================
if __name__ == "__main__":
    suite = TestSuite()
    ctx = {"file": "mars.wav", "duration": 10, "topic_keyword": "Mars"}

    suite.run_scenario("Reflex: Direct Command", "Turn on light", checks=[("intent", "LIGHT_ON")])
    suite.run_scenario("Cognitive: Knowledge Query", "What is the capital of France?", checks=[("intent", "llm_response")])
    suite.run_scenario("Ambiguity: Clarification Request", "Turn on", checks=[("speak", "Did you mean light on?")])
    
    suite.run_scenario("Interrupt: Resume Offer", "Turn on light", context=ctx, checks=[("context_suggestion", "resume_confirmation")])
    suite.run_scenario("Interrupt: Hard Stop", "Stop", context=ctx, checks=[("intent", ["STOP", "CONFIRM_NO"])])
    
    suite.run_scenario("Context: Generic 'Yeah'", "Yeah", context=ctx, checks=[("intent", "CONFIRM_YES")])
    suite.run_scenario("Context: Keyword 'Mars'", "Tell me about Mars please", context=ctx, checks=[("intent", "CONFIRM_YES")])
    suite.run_scenario("Context: Negated 'No Mars'", "No Mars", context=ctx, checks=[("intent", "CONFIRM_NO")])
    
    suite.run_scenario("Logic: Barge-In Mode Execution", "Turn on light", mode="barge_in", checks=[("intent", "LIGHT_ON")])
    
    # 🔧 UPDATE: Use expect_none=True to validate the Silence Guard
    suite.run_scenario("Edge Case: Empty Input", "", expect_none=True)

    suite.print_report()