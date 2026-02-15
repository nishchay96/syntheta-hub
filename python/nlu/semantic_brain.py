import logging
import os
import json
import numpy as np
import sys
import re

# ==================== ✅ FORCE OFFLINE MODE ====================
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
# ===============================================================

# 🔧 NEW IMPORTS FOR PHASE 2
try:
    import spacy
    import nltk
    from nltk.sentiment import SentimentIntensityAnalyzer
    nltk.download('vader_lexicon', quiet=True)
except ImportError:
    pass # Handled in __init__

from sentence_transformers import SentenceTransformer, util

class SemanticBrain:
    def __init__(self):
        self.logger = logging.getLogger("Brain")
        
        # --- PATH CONFIGURATION ---
        BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
        
        # 1. Catalog File
        self.reflex_file = os.path.join(BASE_DIR, "python", "nlu", "reflex_catalog.json")
        
        # 2. Model Path (Offline Asset)
        self.model_path = os.path.join(BASE_DIR, "assets", "models", "all-MiniLM-L6-v2")
        
        self.logger.info(f"Loading Semantic Model from: {self.model_path}...")
        
        # Load the Sentence Transformer (Offline Mode Active)
        try:
            if os.path.exists(self.model_path):
                self.model = SentenceTransformer(self.model_path)
                self.logger.info("✅ Semantic Model Loaded (Offline Mode).")
            else:
                self.logger.warning(f"⚠️ Model not found at {self.model_path}. Fallback to online download...")
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as e:
            self.logger.error(f"❌ Failed to load Semantic Model: {e}")
            raise e

        # 🔧 PHASE 2: Load Cognitive Models (NER & Sentiment)
        self.nlp = None
        self.sentiment = None
        try:
            self.logger.info("Loading Cognitive Models (Spacy + VADER)...")
            self.nlp = spacy.load("en_core_web_sm")
            self.sentiment = SentimentIntensityAnalyzer()
            self.logger.info("✅ Cognitive Models Loaded.")
        except Exception as e:
            self.logger.warning(f"⚠️ Cognitive Models missing: {e}. (Run 'pip install spacy nltk' & download en_core_web_sm)")

        self.commands = []
        self.embeddings = None
        self.phrase_map = [] 
        self.strict_alias_map = {} 
        
        self.reload_catalog()

    # =========================================================================
    # 🧠 MAIN PROCESSOR (The "Waiter" - Prepares the Plate)
    # =========================================================================
    def process(self, text):
        """
        The Main Entry Point.
        Input: Raw Audio Text
        Output: Dictionary {clean_text, intent, entities, emotion}
        """
        clean = self.clean_input(text)
        
        # 1. Reflex Check (Fast Path)
        intent_data = self.infer_intent(clean)
        
        # 2. Cognitive Extraction (Slow Path)
        emotion = self.detect_emotion(clean)
        entities = self.extract_entities(clean)

        return {
            "input": clean,
            "intent": intent_data,  # Can be None if no reflex match
            "emotion": emotion,
            "entities": entities
        }

    # =========================================================================
    # 🔍 PHASE 2: COGNITIVE FUNCTIONS
    # =========================================================================
    def clean_input(self, text):
        """Sanitizes common STT errors."""
        if not text: return ""
        t = text.strip()
        # Common Whisper Hallucinations fix
        t = re.sub(r'(?i)^(you|thank you|thanks|start|stop|subtitles).*', '', t) if len(t) < 5 else t
        return t

    def detect_emotion(self, text):
        """Returns: positive, negative, neutral, or curious"""
        if not self.sentiment: return "neutral"
        
        scores = self.sentiment.polarity_scores(text)
        compound = scores['compound']
        
        if "?" in text: return "curious"
        if compound >= 0.05: return "positive"
        if compound <= -0.05: return "negative"
        return "neutral"

    def extract_entities(self, text):
        """
        Extracts named entities using Spacy.
        Returns: Dict[str, str] -> {'person': 'Nishchay', 'location': 'Lab'}
        """
        if not self.nlp: return {}
        
        doc = self.nlp(text)
        entities = {}
        
        # Map Spacy Labels to Friendly Names
        # GPE = Countries/Cities, ORG = Companies, PERSON = People
        label_map = {
            "PERSON": "person",
            "GPE": "location",
            "LOC": "location",
            "ORG": "organization",
            "DATE": "time",
            "TIME": "time"
        }

        for ent in doc.ents:
            if ent.label_ in label_map:
                key = label_map[ent.label_]
                # If key exists, append (e.g. "Nishchay and Martha")
                if key in entities:
                    entities[key] += f", {ent.text}"
                else:
                    entities[key] = ent.text
                    
        return entities

    # =========================================================================
    # ⚡ EXISTING LOGIC (Reflex/Intent System)
    # =========================================================================
    def reload_catalog(self):
        """Loads the JSON catalog and pre-computes vector embeddings."""
        try:
            if not os.path.exists(self.reflex_file):
                self.logger.error(f"Catalog file NOT FOUND at: {self.reflex_file}")
                return

            with open(self.reflex_file, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict) and "commands" in data:
                    self.commands = data["commands"]
                else:
                    self.commands = data
            
            # Reset Maps
            self.phrase_map = [] 
            self.strict_alias_map = {}
            all_phrases = []
            
            for cmd in self.commands:
                if "strict_aliases" in cmd:
                    for alias in cmd["strict_aliases"]:
                        clean_alias = alias.lower().strip()
                        self.strict_alias_map[clean_alias] = cmd

                if "phrases" in cmd:
                    for phrase in cmd['phrases']:
                        all_phrases.append(phrase)
                        self.phrase_map.append(cmd)
            
            if all_phrases:
                self.logger.info(f"Vectorizing {len(all_phrases)} reflex phrases...")
                self.embeddings = self.model.encode(all_phrases, convert_to_tensor=True)
            else:
                self.embeddings = None
                
        except Exception as e:
            self.logger.error(f"Error loading catalog: {e}")
            self.commands = []

    def infer_intent(self, text, context=None, threshold=0.65):
        """
        The 4-Lane NLU Architecture.
        Priority: Strict Alias > Vector Match > Decomposition Match > LLM Handoff
        """
        clean_text = text.lower().strip()
        if not clean_text:
            return None

        # 🚀 LANE 1: FAST PATH (Strict Exact Match)
        if clean_text in self.strict_alias_map:
            matched_cmd = self.strict_alias_map[clean_text]
            self.logger.info(f"⚡ LANE 1 (Strict): '{clean_text}' -> {matched_cmd['id']}")
            return {
                "intent": matched_cmd['id'],
                "confidence": 1.0,
                "type": matched_cmd.get('type', 'sys'),
                "payload": matched_cmd.get('payload', {}),
                "reply_template": matched_cmd.get('reply_template', "Done."),
                "match_type": "strict"  # 🟢 Zero Assumption: Allowed to execute instantly
            }

        # 🐢 LANE 2: SLOW PATH (Full Sentence Vector Search)
        if self.embeddings is not None:
            input_vec = self.model.encode(clean_text, convert_to_tensor=True)
            cosine_scores = util.cos_sim(input_vec, self.embeddings)[0]
            
            best_score_idx = np.argmax(cosine_scores.cpu().numpy())
            best_score = cosine_scores[best_score_idx].item()
            
            if best_score >= threshold:
                matched_cmd = self.phrase_map[best_score_idx]
                self.logger.info(f"🐢 LANE 2 (Vector): '{clean_text}' -> {matched_cmd['id']} (Conf: {best_score:.2f})")
                return {
                    "intent": matched_cmd['id'],
                    "confidence": best_score,
                    "type": matched_cmd.get('type', 'sys'),
                    "payload": matched_cmd.get('payload', {}),
                    "reply_template": matched_cmd.get('reply_template', "Done."),
                    "match_type": "assumed" # 🟢 Zero Assumption: Forces user confirmation
                }

        # ✂️ LANE 3: DECOMPOSITION (Extract Core Meaning & Retry)
        if self.nlp and self.embeddings is not None:
            doc = self.nlp(clean_text)
            
            # Keep only Verbs, Nouns, Proper Nouns, and Adjectives. Strip out fluff words.
            core_tokens = [token.text for token in doc if token.pos_ in ("VERB", "NOUN", "PROPN", "ADJ")]
            distilled_text = " ".join(core_tokens).strip()
            
            # Only run if we actually reduced the sentence
            if distilled_text and distilled_text != clean_text:
                self.logger.info(f"✂️ LANE 3 (Decomp): Reduced '{clean_text}' -> '{distilled_text}'")
                
                distilled_vec = self.model.encode(distilled_text, convert_to_tensor=True)
                distilled_scores = util.cos_sim(distilled_vec, self.embeddings)[0]
                
                d_best_idx = np.argmax(distilled_scores.cpu().numpy())
                d_best_score = distilled_scores[d_best_idx].item()
                
                if d_best_score >= threshold:
                    matched_cmd = self.phrase_map[d_best_idx]
                    self.logger.info(f"🔍 LANE 3 Win: '{distilled_text}' -> {matched_cmd['id']} (Conf: {d_best_score:.2f})")
                    return {
                        "intent": matched_cmd['id'],
                        "confidence": d_best_score,
                        "type": matched_cmd.get('type', 'sys'),
                        "payload": matched_cmd.get('payload', {}),
                        "reply_template": matched_cmd.get('reply_template', "Done."),
                        "match_type": "assumed" # 🟢 Zero Assumption: Forces user confirmation
                    }

        # 🧠 LANE 4: THE HANDOFF
        self.logger.info(f"❌ NLU Lanes failed for '{clean_text}'. Yielding to LLM.")
        return None

    def compare_against_list(self, text, candidate_list):
        """
        ⚡ FAST MATCHING (For Hotlists)
        """
        clean_text = text.lower().strip()
        if not candidate_list or not clean_text:
            return 0.0, ""

        lower_candidates = [c.lower() for c in candidate_list]
        if clean_text in lower_candidates:
            idx = lower_candidates.index(clean_text)
            return 1.0, candidate_list[idx]

        candidate_embeddings = self.model.encode(candidate_list, convert_to_tensor=True)
        input_vec = self.model.encode(clean_text, convert_to_tensor=True)
        
        cosine_scores = util.cos_sim(input_vec, candidate_embeddings)[0]
        
        best_idx = np.argmax(cosine_scores.cpu().numpy())
        best_score = cosine_scores[best_idx].item()
        
        return best_score, candidate_list[best_idx]