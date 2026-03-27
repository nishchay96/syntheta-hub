import logging
import os
import sys
import json
import re
import requests
import time
from datetime import datetime

sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))
try:
    from core.data_models import GoldenPacket
except ImportError:
    GoldenPacket = dict

logger = logging.getLogger("LLM")

# ============================================================
# CONFIGURATION
# ============================================================
UI_MODEL        = "llama3.2:3b"          # Main brain — hot in GPU
OLLAMA_API_URL  = "http://localhost:11434/api/generate"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"

# Token limits per route — keep responses tight for voice
TOKEN_LIMITS = {
    "general_web_search": 250,   # synthesise web data, heavily truncated
    "general_no_web":     75,    # Conversational — stay snappy
    "sql_metrics":        100,
    "default":            100,
}


class OllamaBridge:
    def __init__(self):
        logger.info(f"LLM Bridge Init | Reflex Core Default: {UI_MODEL}")

    def pre_load(self):
        """Warm up the model and pin it in VRAM on startup."""
        logger.info(f"🔥 Hot-loading {UI_MODEL} into VRAM...")
        try:
            payload = {
                "model": UI_MODEL,
                "prompt": "Syntheta pre-load ping. Reply with 'READY'.",
                "stream": False,
                "keep_alive": -1,
                "options": {"num_predict": 5}
            }
            requests.post(OLLAMA_API_URL, json=payload, timeout=20.0)
            logger.info(f"✅ {UI_MODEL} is now PINNED in VRAM.")
        except Exception as e:
            logger.error(f"❌ Failed to hot-load {UI_MODEL}: {e}")

    # ----------------------------------------------------------
    # JSON EXTRACTION HELPER
    # ----------------------------------------------------------
    def _extract_json(self, text: str) -> dict:
        try:
            text = text.replace("```json", "").replace("```", "").strip()
            # Strip deepseek <think> blocks
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            start = text.find('{')
            end   = text.rfind('}')
            if start != -1 and end != -1:
                return json.loads(text[start:end + 1])
            return json.loads(text)
        except Exception as e:
            logger.error(f"⚠️ JSON Parse Error: {e}")
            return None

    # ----------------------------------------------------------
    # CORE API CALL
    # ----------------------------------------------------------
    def _call_ollama_api(self, prompt: str, system_prompt: str,
                         model_name: str = UI_MODEL,
                         abort_check=None,
                         route_taken: str = "general_no_web") -> dict:

        is_reasoning = "deepseek" in model_name.lower()

        # Dynamic token budget
        predict_limit = TOKEN_LIMITS.get(route_taken, TOKEN_LIMITS["default"])
        if is_reasoning:
            predict_limit = 1024

        # Prompt assembly
        if is_reasoning:
            final_prompt = f"{system_prompt}\n\n{prompt}"
        else:
            final_prompt = (
                f"{system_prompt}\n\n"
                f"USER INPUT: {prompt}\n\n"
                f"JSON OUTPUT:"
            )

        payload = {
            "model":      model_name,
            "prompt":     final_prompt,
            "stream":     True,
            "keep_alive": -1,           # Never evict from GPU
            "options": {
                "temperature": 0.0,
                "num_ctx":     4096 if is_reasoning else 2048,
                "num_predict": predict_limit,
            }
        }
        # Disable strict JSON formatting as it severely slows down logits computation on local hardware.
        # The prompt sufficiently guides the model to output JSON.
        try:
            timeout = 120 if is_reasoning else 90
            response = requests.post(
                OLLAMA_API_URL, json=payload, stream=True, timeout=timeout)
            response.raise_for_status()

            raw_text = ""
            for line in response.iter_lines():
                # Abort check — fires when user speaks during generation
                if abort_check and abort_check():
                    logger.warning(
                        f"🛑 WWD INTERRUPT: Killing {model_name} to free VRAM for UI!")
                    response.close()
                    # Evict reasoning models to free VRAM immediately
                    if is_reasoning:
                        try:
                            requests.post(
                                OLLAMA_API_URL,
                                json={"model": model_name, "keep_alive": 0},
                                timeout=2)
                        except Exception:
                            pass
                    return "ABORTED"
                if line:
                    chunk = json.loads(line)
                    raw_text += chunk.get("response", "")

            if is_reasoning:
                return raw_text

            parsed = self._extract_json(raw_text)
            return parsed if parsed else {
                "response":       "I encountered a formatting error.",
                "active_subject": "system_error",
                "is_action":      False,
                "execute":        None,
            }

        except requests.exceptions.Timeout:
            logger.error(f"❌ Ollama Timeout: {model_name}")
            return None
        except Exception as e:
            logger.error(f"❌ Ollama API Error: {e}")
            return None

    # ----------------------------------------------------------
    # SYSTEM PROMPT BUILDER
    # ----------------------------------------------------------
    def generate_slm_prompt(self, packet: GoldenPacket) -> str:
        model = packet.get("model", UI_MODEL)

        # Deepseek/reasoning pass-through — NightWatchman owns the prompt
        if "deepseek" in model.lower():
            return packet.get('role', 'You are a helpful AI.')

        role        = "You are Syntheta, a highly intelligent, concise, and helpful AI voice assistant."
        history     = packet.get('history',     '')
        memory_tank = packet.get('memory_tank', '')
        memory_ctx  = packet.get('memory_context', '')
        today_str   = datetime.now().strftime('%A, %B %d, %Y')

        # Combine memory sources — context (JSON bucket facts) first,
        # then memory_tank (web data or SQL nomic results)
        memory_block = ""
        if memory_ctx:
            memory_block += f"USER'S PERSONAL FACTS:\n{memory_ctx}\n\n"
        if memory_tank:
            memory_block += f"ADDITIONAL CONTEXT:\n{memory_tank}\n"

        return f"""{role}

### TODAY'S DATE: {today_str}

### CRITICAL RULES:
1. You are a voice assistant. Speak naturally and warmly.
2. NEVER mention your "memory", "database", "context window", or internal systems.
3. NEVER say "I will remember this." Background systems handle this invisibly.
4. Keep your spoken "response" EXTREMELY concise. Under 2 short sentences. Max 20 words. Time is of the essence.
5. If the user asks about themselves, use the MEMORY provided below without attribution.
6. Use TODAY'S DATE above to give time-aware answers. Never guess outdated information.
7. Output STRICTLY in the JSON format below.

### MEMORY (use this to answer personal questions, do not mention it):
{memory_block if memory_block else "No personal context available."}

### CONVERSATION HISTORY:
{history if history else "No prior conversation."}

### REQUIRED JSON OUTPUT:
{{
    "response": "Your natural spoken reply.",
    "active_subject": "1-word topic (e.g. 'greeting', 'weather', 'phone')",
    "is_action": false,
    "execute": null
}}"""

    # ----------------------------------------------------------
    # MAIN ENTRY
    # ----------------------------------------------------------
    def generate(self, packet: GoldenPacket):
        model_to_use  = packet.get("model",       UI_MODEL)
        route_taken   = packet.get("route_taken", "general_no_web")
        user_text     = packet.get("input",       "")
        abort_check   = packet.get("abort_check")

        system_instruction = self.generate_slm_prompt(packet)

        logger.info(
            f"[LLM] START Dispatch: {model_to_use} | Route: {route_taken} | "
            f"Memory Tank Active: {bool(packet.get('memory_tank') or packet.get('memory_context'))}"
        )
        
        start_t = time.perf_counter()
        try:
            result = self._call_ollama_api(
                user_text, system_instruction,
                model_to_use, abort_check, route_taken
            )
            
            elapsed = round((time.perf_counter() - start_t) * 1000, 2)
            logger.info(f"[LLM] DONE Dispatch: {model_to_use} | Time: {elapsed}ms")
            return result
        except Exception as e:
            elapsed = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error(f"[LLM] FAILED Dispatch: {model_to_use} | Time: {elapsed}ms | Error: {e}")
            raise e

    # ----------------------------------------------------------
    # LEGACY ADAPTER — keeps PiManager working unchanged
    # ----------------------------------------------------------
    def think(self, user_text: str, context: list = []) -> str:
        history_str = ""
        if isinstance(context, list):
            for item in context[-3:]:
                if isinstance(item, dict):
                    role    = item.get('role', 'user')
                    content = item.get('content', '') or item.get('text', '')
                    history_str += f"{role}: {content}\n"
                else:
                    history_str += str(item) + "\n"

        packet: GoldenPacket = {
            "role":          "You are Syntheta.",
            "ctx":           "reflex_fallback",
            "history":       history_str,
            "entities":      {},
            "emotion":       "neutral",
            "input":         user_text,
            "memory_tank":   "",
            "memory_context": "",
            "route_taken":   "general_no_web",
            "model":         UI_MODEL,
        }
        result = self.generate(packet)
        if isinstance(result, dict):
            return result.get("response", "")
        return str(result) if result else ""

    def speak(self, raw_thought: str, tone: str = "neutral") -> str:
        return raw_thought