import logging
import sys
import os
import json
import requests

# 🔧 IMPORT DATA MODELS
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))
from core.data_models import GoldenPacket

logger = logging.getLogger("LLM")

# ⚙️ CONFIGURATION
MODEL_NAME = "syntheta-brain" 
OLLAMA_API_URL = "http://localhost:11434/api/generate"

class OllamaBridge:
    def __init__(self):
        logger.info(f"LLM Bridge Init | Unified Core: {MODEL_NAME} (Persistent API Mode)")

    def _call_ollama_api(self, prompt, system_prompt):
        """
        🚀 PERFECTION FIX: Adds keep_alive to prevent VRAM unloading.
        Ensures the model stays 'hot' on the GPU indefinitely.
        """
        payload = {
            "model": MODEL_NAME,
            "prompt": f"{system_prompt}\n\nUser: {prompt}\nAssistant:",
            "stream": False,
            "format": "json",  # 🟢 FIX: Forces Ollama into strict JSON mode
            "keep_alive": -1,  
            "options": {
                "num_predict": 100,
                "temperature": 0.7,
                "top_p": 0.9,
                "num_ctx": 4096 
            }
        }
        
        try:
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=15)
            response.raise_for_status()
            
            result = response.json()
            raw_text = result.get("response", "").strip()
            
            # 🟢 FIX: Parse the JSON string returned by the SLM
            try:
                parsed_json = json.loads(raw_text)
                return parsed_json
            except json.JSONDecodeError:
                logger.warning(f"⚠️ SLM returned malformed JSON. Fallback triggered.")
                return {"response": raw_text, "active_subject": "general"}

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ollama API Error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unknown API Error: {e}")
            return None

    def generate_slm_prompt(self, packet: GoldenPacket) -> str:
        """
        Phase 4: The Wrapper.
        Unzips the Golden Packet into a System Instruction.
        """
        # Safely get fields with defaults
        role = packet.get('role', 'You are Syntheta.')
        ctx = packet.get('ctx', 'general')
        emotion = packet.get('emotion', 'neutral')
        entities = packet.get('entities', {})
        history = packet.get('history', '')

        return (
            f"{role}\n"
            f"--- CONTEXT ---\n"
            f"TOPIC: {ctx}\n"
            f"USER EMOTION: {emotion}\n"
            f"KNOWN ENTITIES: {entities}\n"
            f"--- MEMORY ---\n"
            f"{history}\n"
            "----------------\n"
            # 🟢 FIX: Strict JSON formatting instruction
            "INSTRUCTION: You must respond STRICTLY in JSON format with two keys. "
            "'response': your brief (under 2 sentences), helpful, and friendly conversational reply. "
            "'active_subject': a 1-3 word contextual noun phrase representing the current topic (e.g., 'your feelings', 'the weather', 'black holes')."
        )

    def generate(self, packet: GoldenPacket):
        """
        Unified Processing: Logic + Personality in one shot.
        """
        # 1. Build the Dynamic System Prompt
        system_instruction = self.generate_slm_prompt(packet)
        
        # 2. Extract User Input
        user_text = packet.get('input', '')

        logger.info(f"[LLM] Processing Packet: '{user_text}' | Context: {packet.get('ctx', 'unknown')}")
        
        # 3. Call Ollama via API (Hardware-accelerated)
        response_dict = self._call_ollama_api(user_text, system_instruction)
        
        if not response_dict:
            return {"response": "I'm having trouble connecting to my brain.", "active_subject": "general"}
            
        return response_dict

    # =========================================================
    # 🔧 COMPATIBILITY LAYER (Fixes PiManager Crash)
    # =========================================================
    def think(self, user_text, context=[]):
        """
        Legacy Adapter for PiManager.
        Wraps old-style arguments into a temporary Golden Packet.
        """
        # Convert list context to string
        history_str = ""
        if isinstance(context, list):
            for item in context[-3:]: # Take last 3 turns
                if isinstance(item, dict):
                    role = item.get('role', 'user')
                    content = item.get('content', '') or item.get('text', '')
                    history_str += f"{role}: {content}\n"
                else:
                    history_str += str(item) + "\n"
        
        # Create a dummy packet
        packet: GoldenPacket = {
            "role": "You are Syntheta.",
            "ctx": "reflex_fallback",
            "history": history_str,
            "entities": {},
            "emotion": "neutral",
            "input": user_text
        }
        
        # 🟢 FIX: Unpack the dict to maintain backward compatibility for components expecting a string
        result = self.generate(packet)
        return result.get("response", "")

    def speak(self, raw_thought, tone="neutral"):
        """Passthrough (Already styled by think)"""
        return raw_thought