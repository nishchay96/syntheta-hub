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

try:
    import spacy
    import nltk
    from nltk.sentiment import SentimentIntensityAnalyzer
    nltk.download('vader_lexicon', quiet=True)
except ImportError:
    pass 

from sentence_transformers import SentenceTransformer, util

class SemanticBrain:
    def __init__(self):
        self.logger = logging.getLogger("Brain")
        
        # --- PATH CONFIGURATION ---
        BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
        self.reflex_file = os.path.join(BASE_DIR, "python", "nlu", "reflex_catalog.json")
        self.model_path = os.path.join(BASE_DIR, "assets", "models", "all-MiniLM-L6-v2")
        
        # Load Sentence Transformer
        try:
            if os.path.exists(self.model_path):
                self.model = SentenceTransformer(self.model_path)
                self.logger.info("✅ Semantic Model Loaded (Offline Mode).")
            else:
                self.logger.warning(f"⚠️ Model not found at {self.model_path}. Falling back to online...")
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception as e:
            self.logger.error(f"❌ Critical: Failed to load Semantic Model: {e}")
            raise e

        # Load Spacy & Sentiment
        self.nlp = None
        self.sentiment = None
        try:
            self.nlp = spacy.load("en_core_web_sm")
            self.sentiment = SentimentIntensityAnalyzer()
            self.logger.info("✅ Cognitive Models Loaded (Spacy + VADER).")
        except Exception as e:
            self.logger.warning(f"⚠️ Cognitive Models missing: {e}")

        # --- DATA STORAGE ---
        self.commands = []
        self.meta_topics = []
        
        # Intent Mapping
        self.intent_embeddings = None
        self.intent_phrase_map = [] 
        self.strict_alias_map = {} 
        
        # Topic Mapping (For Fillers)
        self.topic_embeddings = None
        self.topic_category_map = [] 
        
        self.reload_catalog()

    def process(self, text):
        clean = self.clean_input(text)
        
        # 1. Reflex Check (Intents)
        intent_data = self.infer_intent(clean)
        
        # 2. Topic Check 
        topic_category = self.infer_topic(clean)
        
        # 3. Cognitive Extraction
        emotion = self.detect_emotion(clean)
        entities = self.extract_entities(clean)

        return {
            "input": clean,
            "intent": intent_data,  
            "topic": topic_category,
            "emotion": emotion,
            "entities": entities
        }

    def reload_catalog(self):
        try:
            if not os.path.exists(self.reflex_file):
                self.logger.error(f"❌ Catalog file NOT FOUND: {self.reflex_file}")
                return

            with open(self.reflex_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.commands = data.get("commands", [])
                self.meta_topics = data.get("meta_topics", [])

            # --- 1. PROCESS INTENTS (REFLEXES) ---
            self.intent_phrase_map = [] 
            self.strict_alias_map = {}
            intent_phrases = []
            
            for cmd in self.commands:
                if "id" not in cmd: continue
                for alias in cmd.get("strict_aliases", []):
                    self.strict_alias_map[alias.lower().strip()] = cmd
                for phrase in cmd.get("phrases", []):
                    intent_phrases.append(phrase)
                    self.intent_phrase_map.append(cmd)
            
            if intent_phrases:
                self.intent_embeddings = self.model.encode(intent_phrases, convert_to_tensor=True)
                self.logger.info(f"📊 Vectorized {len(intent_phrases)} reflex phrases.")

            # --- 2. PROCESS META-TOPICS (FILLERS) ---
            self.topic_category_map = []
            topic_phrases = []

            for topic in self.meta_topics:
                cat = topic.get("filler_category", "general")
                for phrase in topic.get("phrases", []):
                    topic_phrases.append(phrase)
                    self.topic_category_map.append(cat)

            if topic_phrases:
                self.topic_embeddings = self.model.encode(topic_phrases, convert_to_tensor=True)
                self.logger.info(f"🎭 Vectorized {len(topic_phrases)} topic phrases for fillers.")

        except Exception as e:
            self.logger.error(f"❌ Error reloading catalog: {e}", exc_info=True)

    # =========================================================================
    # ⚡ INFERENCE TRACKS
    # =========================================================================
    # 🟢 FIX: Added 'context=None' to align with PiManager's call signature
    def infer_intent(self, text, context=None, threshold=0.72):
        """Lane 1 (Strict) & Lane 2 (Vector) for Hardware/System reflexes."""
        clean_text = text.lower().strip()
        if not clean_text: return None

        # Lane 1: Strict Match
        if clean_text in self.strict_alias_map:
            cmd = self.strict_alias_map[clean_text]
            return self._build_intent_res(cmd, 1.0, "strict")

        # Lane 2: Vector Search
        if self.intent_embeddings is not None:
            input_vec = self.model.encode(clean_text, convert_to_tensor=True)
            scores = util.cos_sim(input_vec, self.intent_embeddings)[0]
            best_idx = np.argmax(scores.cpu().numpy())
            if scores[best_idx] >= threshold:
                return self._build_intent_res(self.intent_phrase_map[best_idx], scores[best_idx].item(), "assumed")
        
        return None

    def infer_topic(self, text, threshold=0.45):
        clean_text = text.lower().strip()
        if not clean_text or self.topic_embeddings is None:
            return "general"

        input_vec = self.model.encode(clean_text, convert_to_tensor=True)
        scores = util.cos_sim(input_vec, self.topic_embeddings)[0]
        best_idx = np.argmax(scores.cpu().numpy())
        
        score = scores[best_idx].item()
        if score >= threshold:
            category = self.topic_category_map[best_idx]
            self.logger.info(f"🏷️ TOPIC MATCH: {category} (Conf: {score:.2f})")
            return category
            
        return "general"

    def _build_intent_res(self, cmd, score, match_type):
        return {
            "intent": cmd['id'],
            "confidence": score,
            "type": cmd.get('type', 'sys'),
            "payload": cmd.get('payload', {}),
            "reply_template": cmd.get('reply_template', "Done."),
            "match_type": match_type
        }

    # =========================================================================
    # 🔍 COGNITIVE HELPERS
    # =========================================================================
    def clean_input(self, text):
        if not text: return ""
        t = text.strip()
        t = re.sub(r'(?i)^(you|thank you|thanks|start|stop|subtitles).*', '', t) if len(t) < 5 else t
        return t

    def detect_emotion(self, text):
        if not self.sentiment: return "neutral"
        scores = self.sentiment.polarity_scores(text)
        if "?" in text: return "curious"
        if scores['compound'] >= 0.05: return "positive"
        if scores['compound'] <= -0.05: return "negative"
        return "neutral"

    def extract_entities(self, text):
        if not self.nlp: return {}
        doc = self.nlp(text)
        entities = {}
        label_map = {"PERSON": "person", "GPE": "location", "LOC": "location", "ORG": "organization", "DATE": "time"}
        for ent in doc.ents:
            if ent.label_ in label_map:
                key = label_map[ent.label_]
                entities[key] = f"{entities[key]}, {ent.text}" if key in entities else ent.text
        return entities

    def compare_against_list(self, text, candidate_list):
        clean_text = text.lower().strip()
        if not candidate_list or not clean_text: return 0.0, ""
        if clean_text in [c.lower() for c in candidate_list]: return 1.0, clean_text
        
        cand_vecs = self.model.encode(candidate_list, convert_to_tensor=True)
        in_vec = self.model.encode(clean_text, convert_to_tensor=True)
        scores = util.cos_sim(in_vec, cand_vecs)[0]
        idx = np.argmax(scores.cpu().numpy())
        return scores[idx].item(), candidate_list[idx]