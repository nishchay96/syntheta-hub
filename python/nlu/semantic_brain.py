import logging
import os
import json
import re

# Filler words stripped for fuzzy word-set matching
_FILLER_WORDS = {"the", "a", "an", "my", "please", "can", "you", "could", "would"}


class SemanticBrain:
    def __init__(self):
        self.logger = logging.getLogger("Brain")
        
        # --- PATH CONFIGURATION ---
        BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
        self.reflex_file = os.path.join(BASE_DIR, "python", "nlu", "reflex_catalog.json")
        
        # --- DATA STORAGE ---
        self.commands = []
        self.strict_alias_map = {} 
        self.id_to_cmd_map = {} 
        self.wordset_index = []  # [(frozenset, cmd), ...] for fuzzy matching
        
        self.reload_catalog()
        self.logger.info("✅ Semantic Brain Initialized (O(1) Strict + Fuzzy Fallback).")

    def process(self, text):
        clean = self.clean_input(text)
        
        # 1. Fast Strict Intent Check (O(1) Dictionary Lookup)
        # 2. Fuzzy word-set fallback if strict misses
        intent_data = self.infer_intent(clean)
        
        return {
            "input": clean,
            "intent": intent_data,  
            "topic": "general",    # Delegated to Nomic v1.5 in engine.py
            "emotion": "neutral",  # Delegated to Llama 3.2
            "entities": {}         # Delegated to Letta MemoryWorker (DeepSeek)
        }

    def reload_catalog(self):
        try:
            if not os.path.exists(self.reflex_file):
                self.logger.error(f"❌ Catalog file NOT FOUND: {self.reflex_file}")
                return

            with open(self.reflex_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.commands = data.get("commands", [])

            self.strict_alias_map = {}
            self.id_to_cmd_map = {}
            self.wordset_index = []
            seen_wordsets = set()
            
            for cmd in self.commands:
                if "id" not in cmd: continue
                
                # Register ID for SLM Router Lookup
                self.id_to_cmd_map[cmd["id"]] = cmd

                # Index both strict_aliases AND phrases
                triggers = cmd.get("strict_aliases", []) + cmd.get("phrases", [])
                
                for alias in triggers:
                    clean_alias = alias.lower().strip()
                    self.strict_alias_map[clean_alias] = cmd
                    
                    # Build word-set index for fuzzy matching
                    ws = frozenset(
                        w for w in clean_alias.split()
                        if w not in _FILLER_WORDS
                    )
                    if ws and ws not in seen_wordsets:
                        self.wordset_index.append((ws, cmd))
                        seen_wordsets.add(ws)
            
            self.logger.info(
                f"📊 Loaded {len(self.strict_alias_map)} strict aliases + "
                f"{len(self.wordset_index)} fuzzy word-sets into RAM."
            )

        except Exception as e:
            self.logger.error(f"❌ Error reloading catalog: {e}", exc_info=True)

    def get_intent_by_id(self, intent_id):
        """Used if the LLM successfully routes to a known hardware ID."""
        if intent_id in self.id_to_cmd_map:
            cmd = self.id_to_cmd_map[intent_id]
            self.logger.info(f"🧠 SemanticBrain: Recovered payload for ID: {intent_id}")
            return self._build_intent_res(cmd, 1.0, "slm_routed")
        return None

    def infer_intent(self, text, *args, **kwargs):
        """
        Two-stage matching:
          1. O(1) exact dictionary lookup (strict aliases)
          2. Word-set fuzzy match — strips fillers, compares as sets
             Catches word-order variations like "turn light on" = "turn on the light"
        """
        clean_text = text.lower().strip()
        clean_text = re.sub(r'[?.!,]+$', '', clean_text).strip()
        if not clean_text: return None

        # Stage 1: Exact match (O(1))
        if clean_text in self.strict_alias_map:
            cmd = self.strict_alias_map[clean_text]
            return self._build_intent_res(cmd, 1.0, "strict")
        
        # Stage 2: Fuzzy word-set match
        input_words = frozenset(
            w for w in clean_text.split()
            if w not in _FILLER_WORDS
        )
        if not input_words:
            return None

        for alias_ws, cmd in self.wordset_index:
            if input_words == alias_ws:
                self.logger.info(
                    f"🎯 Fuzzy match: '{clean_text}' → {cmd['id']} "
                    f"(words: {alias_ws})"
                )
                return self._build_intent_res(cmd, 0.95, "fuzzy_wordset")
        
        return None

    def _build_intent_res(self, cmd, score, match_type):
        return {
            "intent": cmd['id'],
            "confidence": score,
            "type": cmd.get('type', 'sys'),
            "payload": cmd.get('payload', {}),
            "reply_template": cmd.get('reply_template', "Done."),
            "match_type": match_type
        }

    def clean_input(self, text):
        if not text: return ""
        t = text.strip()
        # Strip common Whisper hallucinations
        t = re.sub(r'(?i)^(you|thank you|thanks|start|stop|subtitles).*', '', t) if len(t) < 5 else t
        return t

    def compare_against_list(self, text, phrase_list):
        """
        Calculates a confidence score (0.0 to 1.0) for the input against a list of phrases.
        Uses word-set intersection for robustness.
        """
        clean_input = self.clean_input(text).lower()
        input_words = set(clean_input.split())
        if not input_words:
            return 0.0, None
            
        best_score = 0.0
        best_phrase = None
        
        for phrase in phrase_list:
            phrase_words = set(phrase.lower().split())
            if not phrase_words: continue
            
            # Intersection score
            intersect = input_words.intersection(phrase_words)
            if not intersect:
                score = 0.0
            else:
                # Jaccard-like or overlap ratio
                score = len(intersect) / len(phrase_words)
                
            if score > best_score:
                best_score = score
                best_phrase = phrase
                
        return best_score, best_phrase