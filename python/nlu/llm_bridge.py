import logging
import sys
import os
import json
import requests


# 🔧 IMPORT DATA MODELS
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))
from core.data_models import GoldenPacket

logger = logging.getLogger("LLM")

# ⚙️ CONFIGURATION: The Proven Titans
UI_MODEL = "syntheta-brain:latest" 
# 🟢 FIX: Upgraded the background task model to the 10/10 Gemma benchmark winner
TASK_MODEL = "syntheta-brain:latest" 
OLLAMA_API_URL = "http://localhost:11434/api/generate"

class OllamaBridge:
    def __init__(self):
        logger.info(f"LLM Bridge Init | Dual-Brain Architecture Active")
        logger.info(f" -> UI Core: {UI_MODEL}")
        logger.info(f" -> Task Core: {TASK_MODEL}")

    def _call_ollama_api(self, prompt, system_prompt, is_task=False):
        """
        Routes to the appropriate model based on task type.
        """
        target_model = TASK_MODEL if is_task else UI_MODEL
        
        payload = {
            "model": target_model,
            "prompt": f"{system_prompt}\n\nUser: {prompt}\nAssistant:",
            "stream": False,
            "format": "json",  # Forces Ollama into strict JSON mode
            "keep_alive": -1,  
            "options": {
                # Task model needs 0.0 temp for pure logic, UI needs 0.7 for personality
                "temperature": 0.0 if is_task else 0.7, 
                "num_predict": 150 if is_task else 100,
                "top_p": 0.9,
                "num_ctx": 4096 
            }
        }
        
        try:
            # Allow slightly longer timeout for background tasks just in case
            timeout_sec = 30 if is_task else 15
            response = requests.post(OLLAMA_API_URL, json=payload, timeout=timeout_sec)
            response.raise_for_status()
            
            result = response.json()
            raw_text = result.get("response", "").strip()
            
            try:
                parsed_json = json.loads(raw_text)
                return parsed_json
            except json.JSONDecodeError:
                logger.warning(f"⚠️ SLM returned malformed JSON. Fallback triggered.")
                return {
                    "response": raw_text, 
                    "active_subject": "general", 
                    "is_action": False, 
                    "execute": None
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ollama API Error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unknown API Error: {e}")
            return None
        
    def generate_slm_prompt(self, packet: GoldenPacket) -> str:
        """
        Phase 4: The Wrapper.
        Unzips the Golden Packet into a System Instruction with Dynamic Overrides.
        """
        ctx = packet.get('ctx', 'general')
        
        # 1. Background Worker Bypass
        if ctx == "memory_consolidation":
            return packet.get('role', 'You are a data extraction AI.')

        # 2. 🟢 FIX: Persona Collision Override
        # If the Librarian Router flagged this as a memory fetch, we force the AI 
        # to focus on the user's data, entirely bypassing its own Lore Book.
        override_topic = packet.get('override_topic')
        if override_topic == "fetch_memory":
            role = (
                "You are Syntheta. The user is asking about the history of this chat. "
                "Use the '--- MEMORY & DATA ---' block to recall the exact sequence of events. "
                "If they ask you to 'Repeat', find your most recent response in the memory and say it again exactly."
            )
        else:
            role = packet.get('role', 'You are Syntheta.')

        emotion = packet.get('emotion', 'neutral')
        entities = packet.get('entities', {})
        history = packet.get('history', '')

        # 3. 🟢 FIX: The "Lazy SLM" Anti-URL Directive
        # If the history buffer contains web data, we append a strict behavioral command.
        web_instruction = ""
        if "LIVE WEB SNIPPETS" in history:
            web_instruction = "\n- WEB DATA DETECTED: You MUST read and synthesize the provided web snippets into your conversational answer. NEVER tell the user to 'visit a link' or 'check a website'."

        return (
            f"{role}\n"
            f"--- CONTEXT ---\n"
            f"TOPIC: {ctx}\n"
            f"USER EMOTION: {emotion}\n"
            f"KNOWN ENTITIES: {entities}\n"
            f"--- MEMORY & DATA ---\n"
            f"{history}\n"
            "----------------\n"
            "INSTRUCTION: You must respond STRICTLY in JSON format with four keys:\n"
            "- 'response': your brief (under 2 sentences), helpful, and friendly conversational reply.\n"
            "- 'active_subject': a 1-3 word contextual noun phrase representing the current topic.\n"
            "- 'is_action': boolean (true if the user is asking to control a physical smart home device like lights, fans, or AC, else false).\n"
            "- 'execute': string of the Home Assistant service to call (e.g., 'light.turn_on', 'fan.turn_off', 'climate.set_temperature') if is_action is true, else null."
            f"{web_instruction}"
        )

    def generate(self, packet: GoldenPacket):
        """
        Unified Processing: Logic + Personality in one shot.
        """
        system_instruction = self.generate_slm_prompt(packet)
        user_text = packet.get('input', '')
        is_memory_task = (packet.get('ctx') == "memory_consolidation")

        logger.info(f"[LLM] Processing Packet: '{user_text}' | Context: {packet.get('ctx', 'unknown')}")
        
        response_dict = self._call_ollama_api(user_text, system_instruction, is_task=is_memory_task)
        
        if not response_dict:
            return {
                "response": "I'm having trouble connecting to my brain.", 
                "active_subject": "general",
                "is_action": False,
                "execute": None
            }
            
        if is_memory_task:
            return {"response": json.dumps(response_dict)}
            
        return response_dict

    def think(self, user_text, context=[]):
        """Legacy Adapter for PiManager."""
        history_str = ""
        if isinstance(context, list):
            for item in context[-3:]: 
                if isinstance(item, dict):
                    role = item.get('role', 'user')
                    content = item.get('content', '') or item.get('text', '')
                    history_str += f"{role}: {content}\n"
                else:
                    history_str += str(item) + "\n"
        
        packet: GoldenPacket = {
            "role": "You are Syntheta.",
            "ctx": "reflex_fallback",
            "history": history_str,
            "entities": {},
            "emotion": "neutral",
            "input": user_text
        }
        
        result = self.generate(packet)
        return result.get("response", "")

    def speak(self, raw_thought, tone="neutral"):
        """Passthrough"""
        return raw_thought