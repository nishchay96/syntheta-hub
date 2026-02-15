import time
import logging
import sys
import os

# Ensure we can find sibling modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from nlu.semantic_brain import SemanticBrain

# ==================== CONFIGURATION ====================
THRESH_CLEAN = 0.80 
THRESH_BARGE = 0.90
THRESH_CONTEXT = 0.60

GLOBAL_CONTEXT_BATCHES = {
    "positive": ["yes", "yeah", "yep", "sure", "okay", "correct", "do it", "go ahead", "continue", "resume"],
    "negative": ["no", "nope", "cancel", "stop", "don't", "skip", "nevermind", "forget it"]
}

class PiManager:
    def __init__(self, engine_state):
        """
        Args:
            engine_state: Reference to the shared EngineState (StateManager) instance.
                          This ensures we use the Single Source of Truth for memory.
        """
        self.engine = engine_state
        self.logger = logging.getLogger("Brain")
        self.reflex_brain = SemanticBrain()
        
        # self.history removed (Redundancy Fix)
        self.current_session_id = {}       
        self.session_start_time = {}       
        self.last_interaction_time = {}    
        self.is_engaged = {}               
        self.pending_action = {}           
        self.pending_action_time = {}      
        self.active_context_batches = {}   

    def _init_sat_state(self, sat_id):
        # Only init flags, not memory
        if sat_id not in self.last_interaction_time:
            self.last_interaction_time[sat_id] = 0
            self.is_engaged[sat_id] = False
            self.pending_action[sat_id] = None
            self.pending_action_time[sat_id] = 0
            self.active_context_batches[sat_id] = None

    def start_new_session(self, sat_id, session_id=None):
        self._init_sat_state(sat_id)
        now = time.time()
        time_since_last = now - self.last_interaction_time.get(sat_id, 0)
        
        self.current_session_id[sat_id] = session_id
        self.session_start_time[sat_id] = now
        
        if time_since_last > 15.0:
            self.pending_action[sat_id] = None
            self.active_context_batches[sat_id] = None
            self.is_engaged[sat_id] = False
            # Note: We do not wipe engine memory here; we let the engine handle its own buffer limits.
            self.logger.info(f"[Pi] Session (Sat {sat_id}): Fresh Start.")
        else:
            self.is_engaged[sat_id] = True
            self.logger.info(f"[Pi] Session (Sat {sat_id}): Conversation Continued.")

    def process_query(self, sat_id, text, mode="clean", interrupted_context=None):
        self._init_sat_state(sat_id)
        self.last_interaction_time[sat_id] = time.time()
        
        if not text or not text.strip():
            return None

        self.logger.info(f"[Pi] Sat {sat_id} Processing: '{text}' | Mode: {mode.upper()}")

        # 1. 🟢 SINGLE SOURCE OF TRUTH: Push to Engine State immediately
        # This replaces self.history.append
        self.engine.update_context(sat_id, user_text=text, new_entities={})

        # 2. 🟢 RETRIEVE CONTEXT: Pull fresh history for the Reflex Brain
        # Helper to map 'content' (State) -> 'text' (Reflex expected format)
        raw_history = self.engine.get_recent_context(sat_id)
        reflex_context = [{"role": h["role"], "text": h["content"]} for h in raw_history]

        current_threshold = THRESH_CLEAN if mode == "clean" else THRESH_BARGE
        
        # ============================================
        # ⚡ LAYER 0: CONTEXTUAL OVER-RULE (Refined)
        # ============================================
        if self.pending_action.get(sat_id):
            self.logger.info(f"⚡ Waiting for confirmation from Sat {sat_id}...")
            
            # 1. Generate Dynamic Intent Keyword
            keyword = self.pending_action[sat_id]['intent'].replace("_", " ").lower()
            
            # 2. Tripartite Batches
            current_batches = {
                "positive": [keyword, "yes", f"yes {keyword}", f"{keyword} on", "proceed", "do it"],
                "negative": ["no", "dont", f"no {keyword}", "stop", "cancel", "nevermind"]
            }

            pos_score, _ = self.reflex_brain.compare_against_list(text, current_batches["positive"])
            neg_score, _ = self.reflex_brain.compare_against_list(text, current_batches["negative"])

            if max(pos_score, neg_score) > THRESH_CONTEXT:
                if pos_score > neg_score:
                    self.logger.info(f"✅ CONFIRMED: Executing {keyword}")
                    plan = self.pending_action[sat_id]
                    self.pending_action[sat_id] = None
                    return plan
                else:
                    self.logger.info(f"❌ REJECTED: Cancelling {keyword}")
                    self.pending_action[sat_id] = None
                    return {"speak": "Okay, I won't do that.", "session_policy": "reflex"}

            if max(pos_score, neg_score) < 0.50:
                self.logger.info("🔄 Non-confirmation detected. Resetting for fresh process.")
                self.pending_action[sat_id] = None
                # Continue to Layer 1...

        # ============================================
        # LAYER 1: REFLEX BRAIN (Standard Commands)
        # ============================================
        # Uses the synchronized reflex_context from EngineState
        reflex_result = self.reflex_brain.infer_intent(text, context=reflex_context, threshold=0.35)
        is_command_phrasing = text.lower().startswith(("turn on", "turn off", "switch", "set", "enable", "disable", "stop"))
        
        plan = None 

        if reflex_result:
            intent = reflex_result['intent']
            score = reflex_result['confidence']
            match_type = reflex_result.get('match_type', 'assumed')

            # --- ZERO ASSUMPTION PROTOCOL ---
            if match_type == "strict" and score >= current_threshold:
                self.logger.info(f"🚀 Strict Match: {intent}")
                plan = {
                    "source": "reflex", "type": reflex_result['type'], "execute": reflex_result['payload'],
                    "speak": reflex_result['reply_template'], "confidence": score, "intent": intent, "session_policy": "reflex"
                }
            
            elif match_type == "assumed" or (is_command_phrasing and score > 0.35):
                self.logger.info(f"🤔 Assumed Match: {intent}. Forcing confirmation.")
                self.pending_action[sat_id] = {
                    "source": "reflex", "type": reflex_result['type'], "execute": reflex_result['payload'],
                    "speak": reflex_result['reply_template'], "confidence": score, "intent": intent, "session_policy": "reflex"
                }
                
                readable_intent = intent.replace("_", " ").lower()
                plan = {
                    "source": "reflex", "type": "conversation", "execute": None,
                    "speak": f"I think you meant {readable_intent}. Should I do that?", 
                    "confidence": score, "session_policy": "conversation",
                    "force_listen": True 
                }

        # ============================================
        # 🟢 LAYER 2: THE COGNITIVE HANDOFF
        # ============================================
        if not plan:
            if is_command_phrasing:
                 return {"source": "reflex", "speak": "I'm not sure which device to control.", "session_policy": "reflex"}
            
            self.logger.info("[Pi] Reflex failed. Handing off to Cognitive Engine...")
            self.is_engaged[sat_id] = True 
            return None # Yields to engine.py for LLM

        # ============================================
        # 🔧 LAYER 3: INTERRUPTION RECOVERY
        # ============================================
        if interrupted_context and plan:
            if plan.get("source") == "reflex" and plan.get("intent") not in ["STOP", "CONFIRM_NO", "CONFIRM_YES"]:
                topic = interrupted_context.get("topic_keyword", "that")
                plan["speak"] = f"{plan.get('speak', '')} ... Should we continue about {topic}?"
                plan["force_listen"] = True

        return plan