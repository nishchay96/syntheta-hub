import time
import logging
from collections import deque
import sys
import os

# Ensure we can find sibling modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from nlu.semantic_brain import SemanticBrain

# ==================== ✅ UNIFIED CONFIGURATION ====================
THRESH_STRICT = 0.85  # Synchronized with Engine high-confidence bypass
THRESH_BARGE = 0.90
THRESH_CONTEXT = 0.65 # Tighter threshold for intent confirmation

GLOBAL_CONTEXT_BATCHES = {
    "positive": ["yes", "yeah", "yep", "sure", "okay", "correct", "do it", "go ahead", "continue", "resume"],
    "negative": ["no", "nope", "cancel", "stop", "don't", "skip", "nevermind", "forget it"]
}

class PiManager:
    def __init__(self, engine_state):
        # 🟢 FIX: 'engine_state' is the EngineState object passed from SynthetaEngine
        self.state = engine_state 
        self.logger = logging.getLogger("Brain")
        self.reflex_brain = SemanticBrain()
        
        self.current_session_id = {}       
        self.session_start_time = {}       
        self.last_interaction_time = {}    
        self.is_engaged = {}               
        self.pending_action = {}           
        self.pending_action_time = {}      
        self.active_context_batches = {}   

    def _init_sat_state(self, sat_id):
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
        
        # 🟢 FIX: Corrected attribute access for EngineState
        raw_history = self.state.get_recent_context(sat_id)
        reflex_context = [{"role": h["role"], "text": h["content"]} for h in raw_history]

        # ============================================
        # ⚡ LAYER 0: CONTEXTUAL CONFIRMATION
        # ============================================
        
        # 1. Check for RESUME_PENDING status from the state manager
        # 🟢 FIX: self.state.resume_pending is the correct path
        if self.state.resume_pending.get(sat_id):
            self.logger.info(f"⚡ [Layer 0] Intercepting Response for Resume Confirmation...")
            pos_score, _ = self.reflex_brain.compare_against_list(text, GLOBAL_CONTEXT_BATCHES["positive"])
            neg_score, _ = self.reflex_brain.compare_against_list(text, GLOBAL_CONTEXT_BATCHES["negative"])

            if max(pos_score, neg_score) > THRESH_CONTEXT:
                if pos_score > neg_score:
                    return {"intent": "RESUME_CONFIRMED", "session_policy": "reflex"}
                else:
                    return {"intent": "RESUME_CANCELLED", "session_policy": "reflex"}

        # 2. Check for internal reflex pending actions
        if self.pending_action.get(sat_id):
            self.logger.info(f"⚡ [Layer 0] Checking confirmation for pending reflex...")
            
            keyword = self.pending_action[sat_id]['intent'].replace("_", " ").lower()
            current_batches = {
                "positive": [keyword, "yes", "do it", "proceed", "go ahead"],
                "negative": ["no", "cancel", "stop", "nevermind"]
            }

            pos_score, _ = self.reflex_brain.compare_against_list(text, current_batches["positive"])
            neg_score, _ = self.reflex_brain.compare_against_list(text, current_batches["negative"])

            if max(pos_score, neg_score) > THRESH_CONTEXT:
                if pos_score > neg_score:
                    cached_action = self.pending_action[sat_id]
                    self.pending_action[sat_id] = None
                    return {
                        "source": "reflex",
                        "type": cached_action.get("type", "ha"),
                        "execute": cached_action.get("execute"),
                        "speak": cached_action.get("speak", "Done."),
                        "intent": cached_action.get("intent"),
                        "session_policy": "reflex"
                    }
                else:
                    self.pending_action[sat_id] = None
                    return {"speak": "Okay, I've cancelled that.", "intent": "CONFIRM_NO", "session_policy": "reflex"}

        # ============================================
        # 🟢 LAYER 1: REFLEX BRAIN (Local Intent)
        # ============================================
        reflex_result = self.reflex_brain.infer_intent(text, context=reflex_context, threshold=0.35)
        is_command_phrasing = text.lower().startswith(("turn on", "turn off", "switch", "set", "enable", "disable", "stop"))
        
        if reflex_result:
            intent = reflex_result['intent']
            score = reflex_result['confidence']
            match_type = reflex_result.get('match_type', 'assumed')

            payload = reflex_result.get('payload', {})
            ha_service = ""
            if isinstance(payload, dict):
                ha_service = f"{payload.get('domain', '')}.{payload.get('service', '')}"

            if match_type == "strict" and score >= THRESH_STRICT:
                self.logger.info(f"🚀 [Strict Match] Executing: {intent}")
                return {
                    "source": "reflex", "type": reflex_result['type'], "execute": ha_service,
                    "speak": reflex_result['reply_template'], "confidence": score, "intent": intent, "session_policy": "reflex"
                }
            
            elif match_type == "assumed" or (is_command_phrasing and score > 0.35):
                self.logger.info(f"🤔 [Assumed Match] {intent} | Conf: {score:.2f}")
                
                self.pending_action[sat_id] = {
                    "source": "reflex", "type": reflex_result['type'], "execute": ha_service,
                    "speak": reflex_result['reply_template'], "confidence": score, "intent": intent, "session_policy": "reflex"
                }
                
                readable_intent = intent.replace("_", " ").lower()
                return {
                    "source": "reflex", "type": "conversation", "execute": None,
                    "speak": f"I think you meant {readable_intent}. Should I do that?", 
                    "confidence": score, "intent": "RECOGNITION_QUESTION", "session_policy": "conversation",
                    "force_listen": True 
                }

        if is_command_phrasing:
             return {"source": "reflex", "speak": "I couldn't identify the device you want to control.", "session_policy": "reflex"}
        
        self.logger.info("[Pi] No reflex match. Handing off to Cognitive Engine.")
        self.is_engaged[sat_id] = True 
        return None